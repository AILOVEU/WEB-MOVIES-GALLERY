# -*- coding: utf-8 -*-
"""
fuliba2023 抓取工具链 — 共享库
职责：路径解析、限速器、断点续传台账、HTTP 会话（代理）、重试退避、文件落盘。

设计约束（来自 总体执行规划.md）：
- 所有对 fuliba2023.net 的请求走 Clash 代理 127.0.0.1:7890，verify=False（等价 --ssl-no-revoke）
- 串行 + 基础间隔 + 随机抖动，连续成功后退避休息
- 429/403/503 指数退避；连续 8 次失败熔断停止
- 幂等：可重复执行，已完成跳过
- 沙盒守卫：直接写目标文件，不用 tmp+os.replace，不用 .part
  （例外：年度容器一篇一文件、一次改一处，中途打断会整篇丢，所以 years.py 内部用 tmp+换名）

** 2026-11-04 目录分层 **（根目录只留站点外壳与资源，其余带下划线的都是工作台）：
    <ROOT>/index.html|style.css|archived.html   站点入口（浏览器直接打开 index.html）
    <ROOT>/assets/                              站点资源；图片库在 assets/images/
    <ROOT>/data/<年>/                           年份文章目录（发布层，一年一个自包含目录）
    <ROOT>/_scripts/                            脚本；共享层在本目录顶层，步骤脚本按功能分子目录
    <ROOT>/_pipeline/                           过程与台账：删了丢进度（urls/manifest/state/resume/config）
    <ROOT>/_pipeline/material/<年>/              该年每篇的原料容器（2026-11-09 从 data/ 迁出，仍一篇一 .jsonl）
    <ROOT>/_rawdata/                            抓回来的原始文件：sitemaps / pages 缓存
    <ROOT>/_logs/<日期>/                          日志与验证回报（一天一份；随时可删，删了不丢数据）
    <ROOT>/_logs/shots/<日期>/                    浏览器/渲染验证的截图（日期在目录上，文件名不再带日期前缀）
    <ROOT>/_backup/<日期>_<事由>/               大改前的快照

年度层（路径与读写一律走 years.py）：
    <ROOT>/data/<年>/data.js                该年列表元数据
    <ROOT>/data/<年>/_audit.json            该年完整性小清单（拷贝后验收）
    <ROOT>/data/<年>/part<i>.html           文章分片（2026-11-08，一篇一锚点；站点根下 2 层 → ../../assets/images/…）
    <ROOT>/_pipeline/material/<年>/<url_key>.jsonl 原料（2026-11-09 从 data/ 迁出）：原始 HTML + 解析产物 + 内部分页子页
图片层：台账与容器里的图片键仍是 `images/<xx>/<hash>.ext`（14 万条一个没改），
实际落点与页内 href 都是 `assets/images/…` —— 换位置只经本模块 site_rel()/img_disk_path() 翻译。
"""
import io
import json
import os
import random
import re
import ssl
import sys
import threading
import time
import urllib.parse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------------------------------- 路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))      # <ROOT>/_scripts
ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(ROOT, "data")          # 年份文章目录（站点发布层）
PIPELINE_DIR = os.path.join(ROOT, "_pipeline")  # 过程与台账：删了丢进度
RAW_DIR = os.path.join(ROOT, "_rawdata")        # 抓回来的原始文件：sitemaps / pages
LOG_DIR = os.path.join(ROOT, "_logs")           # 日志、验证回报与截图：随时可删
BACKUP_DIR = os.path.join(ROOT, "_backup")      # 大改前的快照（按日期建子目录）
SRC = PIPELINE_DIR                              # 旧名兼容：C.SRC 一律指 _pipeline
SITEMAP_DIR = os.path.join(RAW_DIR, "sitemaps")
PAGES_DIR = os.path.join(RAW_DIR, "pages")      # 列表页发现缓存
IMAGES_DIR = os.path.join(ROOT, "assets", "images")
STATE_FILE = os.path.join(SRC, "state.jsonl")
PACER_FILE = os.path.join(SRC, "pacer_state.json")

# 年度化前的旧目录（仅 s07 转换/回退时读，不再自动创建）
LEGACY_HTML_DIR = os.path.join(RAW_DIR, "html")
LEGACY_PARSED_DIR = os.path.join(SRC, "parsed")
LEGACY_SUBPAGE_DIR = os.path.join(SRC, "subpages")
LEGACY_ARTICLES_DIR = os.path.join(ROOT, "articles")
LEGACY_ROOT_IMAGES_DIR = os.path.join(ROOT, "images")   # 2026-11-04 前图片库在根

for _d in (DATA_DIR, PIPELINE_DIR, RAW_DIR, LOG_DIR, SITEMAP_DIR):
    os.makedirs(_d, exist_ok=True)

# ---------------------------------------------------------------- 图片层前缀
# 台账 image_manifest.json 与容器 parsed.images[].local 存的都是 `images/<xx>/<hash>.ext`
# （14 万条，一条都没重写）。图片实际住在 assets/images/ 下，页内 href 与磁盘读取
# 都必须经过下面两个函数翻译，禁止在别处手工拼 "images/" 或 "assets/images/"。
IMG_KEY_PREFIX = "images/"            # 台账/容器里的图片键前缀
IMG_SITE_PREFIX = "assets/images/"    # 站点根相对的真实位置


def site_rel(local):
    """台账图片键 / 其它站点根相对路径 → 站点根相对路径（images/… → assets/images/…）。"""
    if not local or local.startswith(("http://", "https://", "//", "data:")):
        return local
    if local.startswith(IMG_KEY_PREFIX):
        return IMG_SITE_PREFIX + local[len(IMG_KEY_PREFIX):]
    return local


def img_disk_path(local):
    """台账图片键 → 本机真实文件路径（历史图在外部库时可能不存在）。"""
    return os.path.join(ROOT, site_rel(local).replace("/", os.sep))


def img_key_from_disk(path):
    """磁盘路径 → 台账图片键 `images/<xx>/<hash>.ext`（img_disk_path 的逆）。

    ⚠ 这是§8 第 1 条「图片命名不变量」的另一半：**键的前缀只允许由本函数给出**。
    2026-11-04 图片库从根 `images/` 并进 `assets/images/` 后，各处还在用
    `os.path.relpath(p, ROOT)` 造键，于是新条目成了 `assets/images/...`，与 14 万条
    历史键不一致 —— 直接打断 s04d 的幂等判定（它按 `src.startswith("images/")` 认
    「上一轮已回写的本地引用」），第二轮回写会把好图当未落地写成占位图（实测 280 处）。
    """
    rel = os.path.relpath(path, ROOT).replace("\\", "/")
    if rel.startswith(IMG_SITE_PREFIX):
        return IMG_KEY_PREFIX + rel[len(IMG_SITE_PREFIX):]
    return rel


def script_path(rel):
    """步骤脚本相对 _scripts/ 的路径 → 绝对路径（rebuild/update 的流水线表用它）。"""
    return os.path.join(SCRIPT_DIR, rel)


# ---------------------------------------------------------------- _pipeline 的日期目录
# 约定（2026-11-04）：_pipeline/ 根只放**持续读写的单一事实源**（urls.json /
# image_manifest.json / images_store.json / urls_gone.json / skipped_promo.json /
# categories.json / archive_ops.json / pacer*.json），它们按全集判定，拆到日期目录会
# 让「是否已登记」的判据变模糊（死文章会被抓回、历史图会被重下）。
# **一天一份 / 一轮一份** 的东西一律落 _pipeline/<YYYYMMDD>/：
#   state_<阶段>.jsonl（审计线索，追加写）、resume_*.json（续跑档）、
#   urls_pages_pending.json / urls_outofscope.json（逐轮累积的滚动快照）。
DAY_RE = re.compile(r"^\d{8}$")


def pipeline_day_dir(day=None):
    """本轮过程产物的落点 _pipeline/<YYYYMMDD>/（不存在就建，只给写路径用）。"""
    d = os.path.join(PIPELINE_DIR, day or time.strftime("%Y%m%d"))
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


def pipeline_day_dirs():
    """已有的日期目录，按名升序（= 按时间从旧到新）。"""
    try:
        return sorted(n for n in os.listdir(PIPELINE_DIR) if DAY_RE.match(n))
    except OSError:
        return []


def pipeline_find(name):
    """找过程文件：先最新日期目录 → 再倒序其余日期目录 → 再回退 _pipeline/ 根（旧布局）。
    返回绝对路径，找不到返回 None。读取端都用它，日期化搬迁才不会读到空集。"""
    for d in reversed(pipeline_day_dirs()):
        p = os.path.join(PIPELINE_DIR, d, name)
        if os.path.exists(p):
            return p
    p = os.path.join(PIPELINE_DIR, name)
    return p if os.path.exists(p) else None


def pipeline_all(name):
    """同名过程文件的所有副本，按日期升序（旧的在前）。合并读取用。"""
    out = []
    root = os.path.join(PIPELINE_DIR, name)
    if os.path.exists(root):
        out.append(root)              # 旧布局的扁平文件最先读，日期目录里的能覆盖它
    for d in pipeline_day_dirs():
        p = os.path.join(PIPELINE_DIR, d, name)
        if os.path.exists(p):
            out.append(p)
    return out


def pipeline_rolling_path(name):
    """滚动快照的**写路径**（今天的日期目录）；读路径用 pipeline_find。"""
    return os.path.join(pipeline_day_dir(), name)


# ---------------------------------------------------------------- _logs 的日期目录
# 日志也按天分目录：_logs/<YYYYMMDD>/<回报与步骤日志>。跟 _pipeline 同一套寻址：
# 写用 log_path()（落当天，顺手建目录），读用 log_find()（最新一天 → 其余倒序 → 回退根）。
# 这目录随时可删：删了不丢任何数据，只丢历史回报。


def log_day_dir(day=None):
    """本轮日志的落点 _logs/<YYYYMMDD>/（不存在就建，只给写路径用）。"""
    d = os.path.join(LOG_DIR, day or time.strftime("%Y%m%d"))
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


def log_day_dirs():
    """已有的日志日期目录，按名升序（= 从旧到新）。"""
    try:
        return sorted(n for n in os.listdir(LOG_DIR) if DAY_RE.match(n))
    except OSError:
        return []


def log_find(name):
    """找日志/回报：先最新日期目录 → 再倒序其余 → 再回退 _logs/ 根（旧布局）。
    没找到返回 None。用于「上一轮的 report.md 还在不在」这类读侧判定。"""
    for d in reversed(log_day_dirs()):
        p = os.path.join(LOG_DIR, d, name)
        if os.path.exists(p):
            return p
    p = os.path.join(LOG_DIR, name)
    return p if os.path.exists(p) else None


def log_path(name):
    """日志写路径：_logs/<当天 YYYYMMDD>/name（目录自动建）。"""
    return os.path.join(log_day_dir(), name)


# ---------------------------------------------------------------- 验证截图
# 浏览器/渲染验证的截图全部落 _logs/shots/<YYYYMMDD>/：日期在目录上，所以
# 文件名不再强制日期前缀；同一天多张用 <HHMMSS>_<语义>.png 区分。
# 跟日志同一个口径：随时可删，删了不丢任何数据。
SHOTS_DIR = os.path.join(LOG_DIR, "shots")


def shots_day_dir(day=None):
    """本轮验证截图的落点 _logs/shots/<YYYYMMDD>/（不存在就建）。"""
    d = os.path.join(SHOTS_DIR, day or time.strftime("%Y%m%d"))
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


def shot_path(name):
    """截图写路径：_logs/shots/<今天 YYYYMMDD>/name（目录自动建）。"""
    return os.path.join(shots_day_dir(), name)


def shot_find(name):
    """找截图：最新一天 → 其余倒序 → 回退 _logs/shots/ 根。没找到返回 None。"""
    try:
        days = sorted(n for n in os.listdir(SHOTS_DIR) if DAY_RE.match(n))
    except OSError:
        days = []
    for d in reversed(days):
        p = os.path.join(SHOTS_DIR, d, name)
        if os.path.exists(p):
            return p
    p = os.path.join(SHOTS_DIR, name)
    return p if os.path.exists(p) else None

# images 的位置交给 store.py 判定：mode=local 时照旧建目录（与历史行为一致）；
# mode=incremental（历史图已被移到别的电脑，本机拿不到）时也建真实目录，
# 但它只用来接**增量新图**，s04i/s04f 会改按「manifest 有条目 = 已下载」判定；
# mode=external（图片已外迁、本机只剩 junction）时**绝不**创建真实目录
# ——凭空出现的空 assets/images 会让 s04i 以为一张都没落地，把 13.8 万张重下到本机。
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
import store as ST  # noqa: E402
import years as Y   # noqa: E402  年度层：路径 + 一篇一容器读写（不 import common，无循环）

STORE_BOOT = ST.ensure_images_dir_at_import()   # local/attached/detached/incremental/broken/error


def store_state(cfg=None):
    """(state, cfg, detail)，state ∈ local/attached/detached/incremental/broken。"""
    return ST.state(cfg or ST.load_cfg())


def new_images_only(cfg=None):
    """True = 历史图片库不在本机可达位置（mode=incremental）。

    图片步骤据此改成「只处理增量」：manifest 有条目的 URL 视为已下载（不重下），
    文件 stat 不到的条目视为在外部库（不删除、不回写成占位图）。
    """
    return ST.new_images_only(cfg)


def guard_or_exit(step):
    """图片相关步骤的闸门：拔盘/破损态直接退出，不下载、不改 manifest。"""
    return ST.guard_or_exit(step)

# ---------------------------------------------------------------- 常量
BASE = "https://fuliba2023.net"
PROXY = "http://127.0.0.1:7890"
PROXIES = {"http": PROXY, "https": PROXY}

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]
LANGS = ["zh-CN,zh;q=0.9,en;q=0.8", "zh-CN,zh;q=0.9", "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7"]

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def ua():
    return random.choice(UA_POOL)


def headers(ref=None, extra=None):
    h = {
        "User-Agent": ua(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": random.choice(LANGS),
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    if ref:
        h["Referer"] = ref
    if extra:
        h.update(extra)
    return h


def adapters_pool(maxsize=32, pool_block=False):
    """大连接池适配器。

    requests 默认 HTTPAdapter 的 pool_maxsize=10、pool_block=False，
    并发超过 10 时连接会被丢弃重建，表现为「速率上不去 + 大量
    (Read timed out) / Max retries exceeded」。图片下载这类高并发
    场景必须显式放大连接池。
    """
    a = requests.adapters.HTTPAdapter(pool_connections=maxsize,
                                      pool_maxsize=maxsize,
                                      max_retries=0,
                                      pool_block=pool_block)
    return a


_PROXY_WARNED = False


def proxy_alive(host="127.0.0.1", port=7890, timeout=2.0):
    """探测 Clash 代理端口是否可连（只做 TCP 握手，不发请求）。"""
    import socket
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def make_session(proxy=True, pool=32, auto_fallback=True):
    """建会话。proxy=True 时若代理端口不通，自动降级直连（只提示一次）：

    原站 fuliba2023.net 可直连，代理没开是常事；不降级则整条流水线
    会撞一片 ProxyError 然后被熔断，看起来像「站封了我们」。
    图床域名本来就不走代理（s04i 按域直取），不受影响。
    """
    global _PROXY_WARNED
    if proxy and auto_fallback and not proxy_alive():
        proxy = False
        if not _PROXY_WARNED:
            _PROXY_WARNED = True
            log("[common] 代理 127.0.0.1:7890 不通，本次会话降级直连")
    s = requests.Session()
    s.headers.update(headers())
    if proxy:
        s.proxies.update(PROXIES)
    s.verify = False
    if pool:
        s.mount("https://", adapters_pool(pool))
        s.mount("http://", adapters_pool(pool))
    return s


# ---------------------------------------------------------------- 限速器
class Pacer(object):
    """串行限速器：基础间隔 + 随机抖动；连续成功 N 次后休息。状态持久化。"""
    RETRY_STATUS = (429, 403, 503, 502, 504)

    def __init__(self, base=1.2, jitter=0.8, rest_every=50, rest_secs=3.0,
                 name="default", state_file=None):
        self.base = base
        self.jitter = jitter
        self.rest_every = rest_every
        self.rest_secs = rest_secs
        self.name = name
        self.state_file = state_file or PACER_FILE
        self.ok_count = 0
        self.fail_streak = 0
        self._last = 0.0
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        try:
            with io.open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            st = data.get(self.name, {})
            self.ok_count = st.get("ok_count", 0)
            self.fail_streak = st.get("fail_streak", 0)
            self._last = st.get("last_ts", 0.0)
        except Exception:
            pass

    def _save(self):
        with self._lock:
            data = {}
            try:
                with io.open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
            data[self.name] = {
                "ok_count": self.ok_count,
                "fail_streak": self.fail_streak,
                "last_ts": self._last,
            }
            try:
                with io.open(self.state_file, "w", encoding="utf-8") as f:
                    f.write(json.dumps(data, ensure_ascii=False, indent=2))
            except Exception:
                pass

    def wait(self):
        """请求前调用：保证距上次请求至少 base+jitter。

        线程安全实现要点：
        - 用「预占时间窗」而非持锁等待，避免多线程全部堵在锁上（实测会让吞吐塌到 1/5）。
        - 原子操作：读当前时间 → 计算自己的目标时刻 → 立刻写回。
          写回时若发现已有更晚的预约，则顺延，保证请求之间不重叠。
        """
        gap = self.base + random.uniform(0, self.jitter)
        with self._lock:
            now = time.time()
            start = max(now, self._last)
            self._last = start + gap
        wait = start - now
        if wait > 0:
            time.sleep(wait)

    def on_ok(self):
        with self._lock:
            self.ok_count += 1
            self.fail_streak = 0
            need_rest = bool(self.rest_every) and (self.ok_count % self.rest_every == 0)
        if need_rest:
            time.sleep(self.rest_secs)
        self._save()

    def on_fail(self):
        with self._lock:
            self.fail_streak += 1
            streak = self.fail_streak
        self._save()
        # 指数退避 2 4 8 15 30
        delay = [2, 4, 8, 15, 30][min(streak - 1, 4)]
        time.sleep(delay)
        return streak

    def reset_fail(self):
        with self._lock:
            self.fail_streak = 0
        self._save()


# ---------------------------------------------------------------- 熔断
class CircuitBreaker(Exception):
    pass


def fetch(session, url, pacer, tries=5, ref=None, timeout=45, stream=False,
          headers_extra=None, allow_status=(200,)):
    """带限速与退避的 GET。返回 (response, text) 或 (None, None)。"""
    last_err = None
    for attempt in range(tries):
        pacer.wait()
        try:
            r = session.get(url, headers=headers(ref, headers_extra),
                            timeout=timeout, stream=stream, allow_redirects=True)
        except Exception as e:
            last_err = e
            streak = pacer.on_fail()
            if streak >= 8:
                raise CircuitBreaker("连续失败 8 次，熔断: %s (%s)" % (url, e))
            continue

        if r.status_code in allow_status:
            pacer.on_ok()
            if stream:
                return r, None
            r.encoding = r.apparent_encoding or "utf-8"
            return r, r.text

        if r.status_code in Pacer.RETRY_STATUS:
            last_err = "HTTP %d" % r.status_code
            streak = pacer.on_fail()
            if streak >= 8:
                raise CircuitBreaker("连续受限流响应 8 次，熔断: %s (HTTP %d)" % (url, r.status_code))
            continue

        # 4xx 其他：记录但不算限流
        if r.status_code == 404:
            pacer.on_ok()
            return None, None
        last_err = "HTTP %d" % r.status_code
        streak = pacer.on_fail()
        if streak >= 8:
            raise CircuitBreaker("连续失败 8 次，熔断: %s (HTTP %d)" % (url, r.status_code))
    return None, None


# ---------------------------------------------------------------- 台账
def state_append(stage, key, status, **extra):
    """写台账。台账是「审计线索」而非「正确性依赖」，任何写入失败都不得中断主流程。

    踩坑记录：Windows 上其他进程（如编辑器/读取工具）以独占方式打开 state.jsonl 时，
    追加写会抛 PermissionError，曾导致 s02 在 3100/7420 处直接崩掉。
    修复：改为「分阶段独立文件 + 失败重试 + 彻底失败则降级到内存丢弃」，
    台账永远不阻断抓取；断点续传的真实依据是年度容器本身（_pipeline/material/<年>/）。
    """
    rec = {"stage": stage, "key": key, "status": status, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    rec.update(extra)
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    # 台账按天开文件：_pipeline/<YYYYMMDD>/state_<stage>.jsonl（读取端 state_load 会合并全部日期）
    target = os.path.join(pipeline_day_dir(), "state_%s.jsonl" % stage)
    for attempt in range(4):
        try:
            # 用二进制 O_APPEND 单次写：多进程并行时行不会交错撕裂
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)
            return rec
        except (PermissionError, OSError):
            time.sleep(0.3 * (attempt + 1))
    # 最后兜底：写不进去就丢弃这条，不影响主流程
    return rec


def state_load(stage=None):
    """读取台账，返回 {key: record}（同 key 后写覆盖前写）。

    2026-11-04 起台账按天分文件（_pipeline/<YYYYMMDD>/state_<stage>.jsonl），这里
    从旧到新合并（日期目录升序，旧布局的扁平文件最先读），语义与单文件时代一致。
    兼容旧的单文件 state.jsonl。
    """
    out = {}
    if stage:
        candidates = pipeline_all("state_%s.jsonl" % stage)
        candidates.append(STATE_FILE)          # 旧版全局台账
    else:
        candidates = [STATE_FILE]
        for st in ("html", "pagenav", "pages", "parse", "rescue", "site", "sitemap", "urls"):
            candidates.extend(pipeline_all("state_%s.jsonl" % st))
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with io.open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if stage and r.get("stage") != stage:
                        continue
                    out[r["key"]] = r
        except (PermissionError, OSError):
            continue
    return out


# ---------------------------------------------------------------- 本轮增量范围
# 背景（2026-11-04）：update.py 以前每轮都把整个库重跑一遍，只为确认「另外 7232 篇不用动」
# （s04d 165s + s02b 85s + s04f 33s + s06 46s）。现在 update.py 把本轮要处理的 URL 写成
# 一份**范围文件**，各步骤用 `--keys-file=<路径>` 显式接住，只处理这批。
#
# 两条红线：
# 1. **不带 --keys-file 就是全树**（rebuild.py --all / archive.py / 手工单跑都不变），
#    所以半成品自愈、全树兜底能力一直在；范围文件不会被任何脚本「自动发现」，
#    只有 update.py 显式传路径。（不用 pipeline_find 那种「最新一份」的自动回退：
#    拿昨天的范围跑今天的一轮 = 静默漏文，比不提速严重得多。）
# 2. 范围文件落 _pipeline/<YYYYMMDD>/，属「一轮一份」：删了不丢数据，
#    下一轮 update.py 会重写；但 _pipeline 根的 urls.json / image_manifest.json 仍是
#    单一事实源，范围模式不改变它们的读写口径。
SCOPE_NAME = "update_keys.json"


def scope_write(urls, note=""):
    """写本轮范围（今天的日期目录），返回绝对路径。"""
    p = pipeline_rolling_path(SCOPE_NAME)
    write_json(p, {"mode": "scoped",
                   "written": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "note": note,
                   "urls": sorted(set(u for u in urls if u))})
    return p


def scope_read(path):
    """范围路径 → set(urls)。没给路径 / 读不到 / 空集 → None（= 不限定，全树）。

    空集返回 None 是故意的：范围算出 0 篇时宁肯退回全树重收敛，
    也不能「所有步骤都做了什么都没做」却打印成功。
    """
    if not path:
        return None
    d = read_json(path, None) or {}
    if d.get("mode") != "scoped":
        return None
    got = set(d.get("urls") or [])
    return got or None


def scope_keys(urls):
    """URL 集合 → 容器文件名的 16 位哈希集合（给 years.iter_parsed(keys=…) 用）。"""
    return set(url_key(u) for u in (urls or []))


def scope_add(path, url):
    """步骤内部改到了「范围外的已有文章」时追加（返回是否真追加）。

    现有唯一用法：s02b 给旧文章抓到新子页 → drop_parsed 废了解析产物，
    必须让 s03/s04d/s06 也重做这篇，否则它会半新半旧留在库里。
    没传路径（全树模式）或非 scoped 文件 → 不做任何事。
    """
    if not path:
        return False
    d = read_json(path, None) or {}
    if d.get("mode") != "scoped":
        return False
    cur = list(d.get("urls") or [])
    if url in cur:
        return False
    cur.append(url)
    d["urls"] = sorted(set(cur))
    write_json(path, d)
    return True


def scope_arg(name="--keys-file"):
    """从命令行取 `--keys-file=<路径>`；没给返回 None（步骤据此走全树行为）。"""
    for a in sys.argv[1:]:
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def write_json(path, obj):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2))


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_text(path, text):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_text_if_changed(path, text):
    """内容相同就不写（mtime 保持），返回是否真写了。

    迁移工具按 mtime+size 判变化，所以建站步骤必须「只写真正变过的页」：
    新增 1 篇时不能让 7 千个单篇页全部变新而全部重传。
    """
    try:
        if os.path.exists(path):
            with io.open(path, "r", encoding="utf-8", newline="") as f:
                if f.read() == text:
                    return False
    except OSError:
        pass
    write_text(path, text)
    return True


def log(*args):
    msg = " ".join([str(a) for a in args])
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def url_key(url):
    """URL → 稳定短哈希（16 位），年度容器的文件名。"""
    import hashlib
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


# 年度化后已没有「原始 HTML 单独一个文件」的位置：原料统一存在
# _pipeline/material/<年>/<url_key>.jsonl 里，读写走 years.py（C.Y.write_raw / read_raw）。
# 旧的 html_path() 已删除，调用方全部改用容器 API（s02/s02b/s03/update/archive）。
