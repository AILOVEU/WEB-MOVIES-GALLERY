# -*- coding: utf-8 -*-
"""
s04j_rescue.py — 未落地图片抢救（走代理 + 域名级 referer + 死域自动拉黑 + 畸形 URL 重建）

背景：历次回补时 Clash 代理是关的，脚本降级直连，导致一批「只是被墙、其实活着」的
第三方图床被误判失败；另有 403 是因 referer 不对。现代理已通，本脚本专门做最后一轮抢救：
  - 只处理当前未落地的图片 URL（按 manifest+磁盘判定）。
  - 强制走代理（proxy_alive 时），失败自动直连兜底。
  - 按域名给正确 Referer（虎嗅/京东/腾讯/阿里…），破解防盗链 403。
  - sinaimg 走 sina_variants（原图→mw690→small）。
  - 魔数校验：非图片(HTML/.bin)一律丢弃，绝不落盘（§8.2）。
  - 死域自动拉黑：某域名连续失败达阈值且零成功 → 跳过其剩余条目，省时不打爆。
  - 畸形 URL（双协议拼接）：重建候选 —— jsdelivr GitHub 镜像 / 内层真实链接；
    命中后**按原始 URL 哈希落盘**，保证 manifest→s04d 的映射仍然对得上。

用法：
  _scripts\\py.cmd _scripts\\images\\s04j_rescue.py --scan         只列未落地清单与域名分布，零网络
  _scripts\\py.cmd _scripts\\images\\s04j_rescue.py                 全量抢救（默认 workers=6）
  _scripts\\py.cmd _scripts\\images\\s04j_rescue.py --hosts=1       先按域名各抽 1 个探路再全量
之后照常 s04f→s04d→s06 让新落地的图在页面生效。
"""
import os
import re
import sys
import time
import glob
import hashlib
import threading
import collections
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C
import s04b_image_policy as POL
import s04i_fetch_body_fast as FI

import requests
import urllib3
urllib3.disable_warnings()

MPATH = os.path.join(C.SRC, "image_manifest.json")

# 域名关键字 → Referer（破解防盗链 403）
REFER = [
    ("sinaimg", "https://weibo.com/"),
    ("huxiu", "https://www.huxiu.com/"),
    ("360buyimg", "https://www.jd.com/"),
    ("jd.com", "https://www.jd.com/"),
    ("jd.hk", "https://www.jd.com/"),
    ("gtimg", "https://new.qq.com/"),
    ("qpic", "https://new.qq.com/"),
    ("gitee", "https://gitee.com/"),
    ("bdstatic", "https://www.baidu.com/"),
    ("pstatp", "https://www.douyin.com/"),
    ("douyinpic", "https://www.douyin.com/"),
    ("maocdn", "https://www.maocdn.com/"),
    ("qhimg", "https://image.so.com/"),
    ("qhmsg", "https://image.so.com/"),
    ("alicdn", "https://www.taobao.com/"),
    ("taobaocdn", "https://www.taobao.com/"),
]
DEFAULT_REF = "https://fuliba.net/"

# 明确已死的自有图床主机（TLS EOF / 502 / 全站 404）——直接不试，省时间
DEAD_HOSTS = {
    "tu.fuliba2021.com", "1.fuliba2021.com", "img.fuliba2021.com",
    "img.fuliba2022.net", "fuliba2022.net", "fuliba2025.net",
    "i12.tietuku.cn", "img.tietuku.com",
}


def host_of(u):
    try:
        return u.split("/")[2].lower()
    except Exception:
        return "?"


def ref_for(host):
    for kw, r in REFER:
        if kw in host:
            return r
    return DEFAULT_REF


def inner_urls(u):
    """畸形双协议 URL → 拆分出候选真实链接。返回按可能性排序的候选列表。"""
    m = list(re.finditer(r"https?://", u))
    if len(m) <= 1:
        return [u]
    cands = []
    # 内层完整链接（最后一个 scheme 起）
    cands.append(u[m[-1].start():])
    # jsdelivr GitHub 镜像重建：https://cdn.jsdelivr.net/gh/<repo>@<ref> + 内层图片路径
    jm = re.search(r"(https://cdn\.jsdelivr\.net/gh/[^h]+@[^/]+)", u)
    if jm:
        tail = u[m[-1].start():]
        path = "/".join(tail.split("/")[3:]) if tail.count("/") >= 3 else ""
        if path:
            cands.append(jm.group(1) + "/" + path)
    return cands


def has_disk(u):
    h = hashlib.sha1(u.encode("utf-8")).hexdigest()[:20]
    for p in glob.glob(os.path.join(C.IMAGES_DIR, h[:2], h + ".*")):
        try:
            if os.path.getsize(p) > 0:
                return True
        except OSError:
            pass
    return False


def collect_missing():
    man = C.read_json(MPATH, {}) or {}
    out = []
    for _u, _k, _slot, a in C.Y.iter_parsed():
        pool = list(a.get("images", []) or [])
        for pg in a.get("pages", []) or []:
            pool.extend(pg.get("images", []) or [])
        for im in pool:
            u = im.get("url")
            if not u:
                continue
            loc = im.get("local")
            if loc:
                fp = C.img_disk_path(loc)
                if os.path.exists(fp) and os.path.getsize(fp) > 0:
                    continue
            if has_disk(u):
                continue
            out.append(u)
    return sorted(set(out))


def mk_session():
    s = requests.Session()
    s.trust_env = False
    s.verify = False
    if C.proxy_alive():
        s.proxies.update(C.PROXIES)
    return s


_tls = threading.local()


def sess():
    s = getattr(_tls, "s", None)
    if s is None:
        s = mk_session()
        _tls.s = s
    return s


_lock = threading.Lock()
_local = {}
_cnt = collections.Counter()
_host_fails = collections.Counter()
_host_ok = collections.Counter()
_blacklist = set()
FAIL_K = 15  # 某域失败达此数且零成功 → 拉黑


def fetch_store(u):
    h = host_of(u)
    with _lock:
        if h in _blacklist or h in DEAD_HOSTS:
            _cnt["skip_black"] += 1
            return
    cands = ([u] + FI.sina_variants(u)) if "sinaimg" in h else inner_urls(u)
    for cu in dict.fromkeys(cands):        # 去重保序
        try:
            r = sess().get(cu, headers=C.headers(ref_for(h)), timeout=18)
        except Exception:
            continue
        try:
            if r.status_code != 200:
                code = r.status_code
                continue
            data = r.content
            if not data or len(data) < 100:
                continue
            if POL.too_many_pixels(data):
                with _lock:
                    _cnt["skip_bigpix"] += 1
                return
            cdata, cext = POL.compress_bytes(data, r.headers.get("Content-Type", ""))
            if cdata is not None:
                data, ext = cdata, cext
            else:
                ext = FI.ext_of(data, r.headers.get("Content-Type", ""))
                if ext == ".bin":          # §8.2 非图片，丢弃
                    with _lock:
                        _cnt["skip_notimg"] += 1
                    return
                if len(data) > POL.HARD_KEEP_KB * 1024:
                    with _lock:
                        _cnt["skip_huge"] += 1
                    return
            path = FI.store_image(u, ext, data)   # 按【原始 URL】落盘，映射对得上
            with _lock:
                _local[u] = {"local": FI.rel_from_root(path),
                             "bytes": os.path.getsize(path),
                             "ctype": r.headers.get("Content-Type", ""),
                             "ok": True, "via": "rescue", "src": cu}
                _cnt["ok"] += 1
                _host_ok[h] += 1
            return
        finally:
            try:
                r.close()
            except Exception:
                pass
    with _lock:
        _cnt["fail"] += 1
        _host_fails[h] += 1
        if _host_fails[h] >= FAIL_K and _host_ok[h] == 0:
            _blacklist.add(h)


def main():
    C.guard_or_exit("s04j")          # 抢救脚本写图 + 写 manifest，必须先确认库可达
    if C.new_images_only():
        # 历史图已被移到别的电脑：本机 stat 不到文件属正常，不是「未落地」。
        # 此时跑抢救会把 13.8 万张历史图当死链重下，必须直接拒跑（挂库后再跑）。
        C.log("[s04j] 图片库模式=incremental：历史图在外部库，本轮不跑抢救；"
              "需要抢救请先把库挂回本机（mode=external + external_root）")
        print("S04J_SKIP_INCREMENTAL")
        return 0
    args = sys.argv[1:]
    workers = next((int(a.split("=", 1)[1]) for a in args if a.startswith("--workers=")), 6)
    missing = collect_missing()
    byhost = collections.Counter(host_of(u) for u in missing)
    C.log("[s04j] 未落地 %d 张，涉及 %d 个域名 | 代理=%s"
          % (len(missing), len(byhost), "on" if C.proxy_alive() else "off(直连兜底)"))
    if "--scan" in args:
        for h, n in byhost.most_common(40):
            print("   %-32s %d" % (h, n))
        return 0

    t0 = time.time()
    todo = [u for u in missing if host_of(u) not in DEAD_HOSTS]
    C.log("[s04j] 跳过已知死域 %d 张，本轮实际尝试 %d 张" % (len(missing) - len(todo), len(todo)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fetch_store, u) for u in todo]
        for i, f in enumerate(futs):
            f.result()
            if (i + 1) % 400 == 0:
                el = time.time() - t0
                C.log("[s04j] 进度 %d/%d ok=%d fail=%d 拉黑域=%d %.1f/s"
                      % (i + 1, len(todo), _cnt["ok"], _cnt["fail"],
                         len(_blacklist), (i + 1) / max(el, 0.01)))

    if _local:
        disk = C.read_json(MPATH, {}) or {}
        disk.update(_local)
        C.write_json(MPATH, disk)
    C.log("[s04j] 完成：抢救成功 %d | 失败 %d | 丢弃(非图/超大/巨像素) %d | 跳过死域 %d | 用时 %.0fs"
          % (_cnt["ok"], _cnt["fail"],
             _cnt["skip_notimg"] + _cnt["skip_huge"] + _cnt["skip_bigpix"],
             _cnt["skip_black"], time.time() - t0))
    print("S04J_OK %d %d" % (_cnt["ok"], _cnt["fail"]))
    C.state_append("rescue", "all", "DONE", ok=_cnt["ok"], fail=_cnt["fail"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
