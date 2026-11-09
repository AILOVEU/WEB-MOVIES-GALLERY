# -*- coding: utf-8 -*-
"""登录墙站点爬虫通用工具箱（从 JavDB 本地化工程沉淀）。

本项目状态（2026-11-03 定：保留但不复用）
  JAVBUS 真正在用的是 `_scripts/inc/crawl.py`，它是**独立的并行实现**，没有 import 本文件。
  两边的 curl / Pace / classify / fetch 同名但不同参数，差异是有意为之：
    - classify：本库先判登录、再判 Cloudflare，短页阈值 5000 B，默认登录标记是 JavDB 的 sign_in；
      crawl.py 先认内容锚点（Discuz 登录态 nav 也含 action=logout，按登录标记先判会误判），
      短页阈值 800 B，限流短语多了数条繁简变体。
    - Pace：本库起步 2.5s / 上限 25s / 冷却 60~120s（JavDB 数万请求实测值）；
      crawl.py 是 1.2s / 20s / 50~100s（本站低流量、页更小）。
    - fetch：本库返回 (html, verdict) 不落盘；crawl.py 直接写盘并只回 verdict。
  ⇒ **调限速、限流短语、登录墙特征时两边要一起改**，只改一边就会漂移。
  本库独有的 anchors_split() / load_window_js() / save_window_js() / merge_entry() / norm_code()
  在 JAVBUS 无人调用（页面解析走 common/discuz.py，交付层写文件在 site/build.py 里自拼），
  留着当参考实现与未来脚本的坑位经验；不要为了“去重”把 crawl.py 硬接到本库上——那会动到实战抓取路径。

六项能力，全部是踩过坑的通用形态（站点特定的正则留在各项目里）：
1. curl()      —— Windows 下经代理抓页：必加 --ssl-no-revoke（schannel 离线吊销检查
                   会报 CRYPT_E_REVOCATION_OFFLINE）；requests 遇沙盒代理会失败时用 curl 兜底。
2. Pace        —— 自适应限速器：单请求 >8s 说明站点吃力 -> delay×1.6（封顶25s）；
                   正常 -> ×0.88 缓慢回落；命中限流特征 -> delay×2.2 + 60~120s 冷却；
                   每 N 个请求强制长休。（实测：从 2.5s 起步可稳定跑数万请求）
3. classify()  —— 响应五级分层判定（勿回退成"空即限流"）：
                   ok=完整页；end=空列表页（分页正常的终止信号，不是限流！）；
                   truncated=截断页（有<title>无</html>，短退避重试）；
                   blocked=CF/限流特征页（长冷却）；
                   login=登录墙（cookie 过期 -> 上层 save 后 exit(2) 等人工重抽）。
4. anchors_split() —— 锚点切块解析法：页面按正则收集所有 item 锚点位置，相邻锚点间
                   切块再抽字段。**必须用此法而非"按 item split"**——列表最后一块
                   常被页面尾部截断，split 法会静默丢数据（实测 583 部误判成 40 部）。
5. load_window_js()/save_window_js() —— window.X = {...}; 数据文件读写：
                   文件头注释含 `=`，split("=",1) 会炸；用 index(var)+raw_decode。
6. merge_entry() —— 字段级幂等合并：标量取更新的非空值、list 字段并集、
                   local(已下载) 标记保留；重复跑无副作用。

机器休眠会挂死 curl 数小时（超时不计休眠时间）-> 配合「监管循环」：
  while($true){ python spider.py; if($LASTEXITCODE -eq 0){break}; if($LASTEXITCODE -eq 2){break}; Start-Sleep 5 }
沙盒文件守卫：os.replace/临时文件/.part/批量删除 可能被拦截并中断进程 -> 直接写目标文件。
"""
import json
import random
import re
import subprocess
import time

UA_DEFAULT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


# ---------------- curl 抓取 ----------------

def curl(url, cookie=None, referer=None, proxy="http://127.0.0.1:7890",
         ua=UA_DEFAULT, timeout=45, headers=None, to_file=None):
    """经代理 GET，返回响应文本（to_file 时返回是否成功）。失败返回 ""。"""
    cmd = ["curl", "-sL", "--ssl-no-revoke", "--max-time", str(timeout)]
    if proxy:
        cmd += ["-x", proxy]
    cmd += ["-A", ua]
    if cookie:
        cmd += ["-H", "Cookie: " + cookie]
    if referer:
        cmd += ["-H", "Referer: " + referer]
    for k, v in (headers or {}).items():
        cmd += ["-H", "%s: %s" % (k, v)]
    if to_file:
        cmd += ["-o", to_file]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore")
        return r.returncode == 0
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore")
    return r.stdout or ""


# ---------------- 自适应限速 ----------------

class Pace:
    def __init__(self, start=2.5, lo=1.5, hi=25.0, every=150, rest=(15, 30)):
        self.delay, self.lo, self.hi = start, lo, hi
        self.every, self.rest = every, rest
        self.n = 0

    def ok(self, dur):
        self.n += 1
        self.delay = min(self.hi, self.delay * 1.6) if dur > 8 \
            else max(self.lo, self.delay * 0.88)
        time.sleep(random.uniform(self.delay * 0.8, self.delay * 1.3))
        if self.n and self.n % self.every == 0:
            w = random.uniform(*self.rest)
            time.sleep(w)

    def blocked(self):
        self.n += 1
        self.delay = min(self.hi, max(self.delay * 2.2, 6.0))
        time.sleep(random.uniform(60, 120))


# ---------------- 响应分层判定 ----------------

CF_MARKS = ("Just a moment", "cf-browser-verification", "Checking if the site connection",
            "Attention Required", "enable JavaScript and cookies")
RATE_MARKS = ("Too Many Requests", "稍後再試", "请求过于频繁", "請稍後再試",
              "更新，請耐心等待")


def classify(html, login_marks=("sign_in",), min_ok=5000,
             list_anchor='<div class="item">'):
    """-> 'ok'|'end'|'truncated'|'blocked'|'login'
    end：完整渲染但列表锚点为 0（末页），是正常终止信号，不要冷却。"""
    if not html:
        return "truncated"
    head = html[:3000]
    if any(s in head for s in login_marks):
        return "login"
    if any(s in html for s in CF_MARKS) or any(s in html for s in RATE_MARKS):
        return "blocked"
    if "</html>" in html and len(html) > min_ok:
        return "ok" if html.count(list_anchor) else "end"
    if "<title" in html:
        return "truncated"
    return "blocked"


def fetch(url, pace, **curl_kw):
    """带重试的抓取。返回 (html, verdict)。verdict=login 时上层应立即存盘 exit(2)。"""
    for attempt in range(6):
        t0 = time.time()
        h = curl(url, **curl_kw)
        dur = time.time() - t0
        v = classify(h)
        if v == "ok":
            pace.ok(dur)
            return h, "ok"
        if v == "end":
            pace.ok(dur)
            return h, "end"
        if v == "login":
            return "", "login"
        if v == "truncated" and attempt < 2:
            time.sleep(random.uniform(6, 12))
            continue
        pace.blocked()
    return "", "fail"


# ---------------- 锚点切块解析 ----------------

def anchors_split(html, anchor_re, fields):
    """anchor_re: 匹配每个条目起始锚点的正则（捕获组按 fields 顺序无关，只用位置）；
    fields: {name: (compiled_re, group, conv)}，conv: str/float/int/strip/upper 之一或函数。
    返回按页面顺序的 dict 列表。跨嵌套 div 稳健；末块截断只影响末条不波及全体。"""
    conv_map = {"str": str, "float": float, "int": int,
                "strip": lambda s: s.strip(),
                "upper": lambda s: re.sub(r"\s+", " ", s).strip().upper()}
    anchors = [m.start() for m in anchor_re.finditer(html)]
    out = []
    for i, s in enumerate(anchors):
        block = html[s:anchors[i + 1] if i + 1 < len(anchors) else len(html)]
        rec = {}
        for name, (rx, grp, conv) in fields.items():
            m = rx.search(block)
            if not m:
                continue
            try:
                rec[name] = conv_map.get(conv, conv)(m.group(grp)) if grp else \
                    conv_map.get(conv, conv)(m.group(0))
            except Exception:
                continue
        if rec:
            out.append(rec)
    return out


# ---------------- window.X = {...} 数据文件 ----------------

WIN_VAR_RE = re.compile(r"window\.(\w+)\s*=\s*")


def load_window_js(path):
    """读 window.X = <json>; —— 容忍文件头注释里的 `=`。返回 (var_name, obj)。"""
    import io
    t = io.open(path, encoding="utf-8").read()
    m = WIN_VAR_RE.search(t)
    if not m:
        raise ValueError("no window.X = found in " + path)
    obj, _ = json.JSONDecoder().raw_decode(t[m.end():].lstrip())
    return m.group(1), obj


def save_window_js(path, var, obj, header="/* 自动生成，勿手改 */", indent=None,
                   separators=(",", ":")):
    """直接写目标文件（不要 tmp+os.replace —— 沙盒守卫会中断进程）。"""
    import io
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        f.write("window.%s = " % var)
        f.write(json.dumps(obj, ensure_ascii=False, indent=indent,
                           separators=separators))
        f.write(";\n")


# ---------------- 字段级幂等合并 ----------------

def is_empty(v):
    return v is None or v == "" or v == [] or v == {}


def merge_entry(existing, incoming, scalars, lists=("tags",), keep=("local",)):
    """同字段取 incoming（更新的一次）非空值；单边非空增量保留；list 取并集
    （incoming 优先保序）；keep 字段 existing 优先（已下载标记不被冲掉）。幂等。"""
    out = dict(existing or {})
    for f in scalars:
        a = (existing or {}).get(f)
        b = incoming.get(f)
        if is_empty(a) and not is_empty(b):
            out[f] = b
        elif not is_empty(a) and not is_empty(b):
            out[f] = b
    for f in lists:
        seq = list(incoming.get(f) or [])
        for x in (existing or {}).get(f) or []:
            if x not in seq:
                seq.append(x)
        if seq:
            out[f] = seq
    for f in keep:
        if (existing or {}).get(f):
            out[f] = existing[f]
    return out


def norm_code(c):
    """作品编号规范化：空白折叠 + 大写。跨源匹配 key 必须用它（大小写/空格变体多）。"""
    return re.sub(r"\s+", " ", (c or "").strip()).upper()
