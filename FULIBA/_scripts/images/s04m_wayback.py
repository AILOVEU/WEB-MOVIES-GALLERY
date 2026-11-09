# -*- coding: utf-8 -*-
"""
s04m_wayback.py — Wayback Machine 存档抢救（最后一道兜底）

背景：s04j 常规抢救后未落地 7354 张，全部为真死链（源站失联/404/畸形）。
经抽样验证 web.archive.org 对这批图床 URL 有存档（tu.fuliba2021 等），
本脚本走「CDX 批量查档 → 精确匹配 → 限速下载」三段式，把请求数从 7354
压到 ~100（CDX 每域 1 次）+ 命中数，避开 archive.org 严格限流（429）。

流程：
  1) collect_missing()（复用 s04j 口径：parsed 引用 + local 缺失 + 磁盘无哈希文件）
  2) 按域名 CDX prefix 查询（filter=statuscode:200 + mimetype:image/*，collapse=urlkey）
     → 规范化 URL 匹配出「有快照」的未落地图
  3) 下载 https://web.archive.org/web/<ts>id_/<original>（原始字节，无改写注入）
     - 全局限速 ≥1.2s/请求；429 全局退避 45s；魔数校验拒 HTML/.bin
     - 按原始 URL sha1[:20] 命名落盘（与全站命名一致，manifest→s04d 映射不断）
  4) 更新 image_manifest.json（src:"wayback"），明细落 _pipeline/<日期>/resume_wayback.json

用法：
  _scripts\\py.cmd _scripts\\images\\s04m_wayback.py --scan      只做 CDX 查档，报命中率，不下载
  _scripts\\py.cmd _scripts\\images\\s04m_wayback.py             查档 + 下载全量
  _scripts\\py.cmd _scripts\\images\\s04m_wayback.py --retry     跳过 CDX，直接重试上次无档/失败清单
  _scripts\\py.cmd _scripts\\images\\s04m_wayback.py --mutant    只处理畸形双协议 URL：内层重建→
                                              带域级 referer 原站下载（代理+直连）→
                                              失败再 Wayback（按内层 host 正确查档）
之后照常 s04f→s04d→s06 让新落地的图在页面生效。
"""
import os
import re
import sys
import json
import glob
import time
import hashlib
import threading

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

import requests

MPATH = os.path.join(C.SRC, "image_manifest.json")
# 续跑档（不是日志，删了会重下已抢回的图）：读最近一份（可能是往日），写今天的日期目录
RESUME_NAME = "resume_wayback.json"


def resume_prev():
    """最近一份续跑档的绝对路径（没有返回 None）。"""
    return C.pipeline_find(RESUME_NAME)


def resume_out():
    """本轮续跑档的落点：_pipeline/<YYYYMMDD>/resume_wayback.json。"""
    return C.pipeline_rolling_path(RESUME_NAME)
PROXIES = {"https": C.PROXY, "http": C.PROXY}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PN", "png"), (b"GIF8", "gif"), (b"RIFF", "webp"))
MIN_BYTES = 500

_rate_lock = threading.Lock()
_last_req = [0.0]
_net_fail_streak = [0]


def host_of(u):
    try:
        return u.split("/")[2].split(":")[0].lower()
    except Exception:
        return "?"


def norm(u):
    """规范化 URL 用于快照匹配：去 scheme、默认端口、尾空白；host 小写。"""
    u = (u or "").strip()
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/]+)(/.*)?$", u)
    if not m:
        return u.lower()
    host, path = m.group(1), m.group(2) or "/"
    host = host.lower()
    host = re.sub(r":(80|443)$", "", host)
    return host + path


def rate_wait():
    """全局限速：任意两请求间隔 ≥1.2s；Wayback 连续网络故障时额外冷却（上限 20s）。"""
    with _rate_lock:
        gap = 2.0 - (time.time() - _last_req[0])
        if gap > 0:
            time.sleep(gap)
        if _net_fail_streak[0] >= 5:
            time.sleep(min(20, 2 * _net_fail_streak[0]))
        _last_req[0] = time.time()


def mark_ok():
    with _rate_lock:
        _net_fail_streak[0] = 0


def mark_fail():
    with _rate_lock:
        _net_fail_streak[0] += 1


def mk_session():
    s = requests.Session()
    s.trust_env = False
    s.verify = False
    s.headers.update({"User-Agent": UA})
    s.proxies.update(PROXIES)
    return s


_tls = threading.local()


def sess():
    s = getattr(_tls, "s", None)
    if s is None:
        s = mk_session()
        _tls.s = s
    return s


def cdx_query(host, tries=4):
    """一个域的 CDX prefix 查询 → {norm(original): timestamp}（取最新 200 图快照）。"""
    params = {"url": "%s/*" % host, "output": "json", "collapse": "urlkey",
              "filter": ["statuscode:200", "mimetype:image/.*"],
              "limit": "200000"}
    for i in range(tries):
        rate_wait()
        try:
            r = sess().get("https://web.archive.org/cdx/search/cdx",
                           params=params, timeout=180)
            if r.status_code == 429:
                C.log("[s04m] CDX 429，退避 45s（%s）" % host)
                time.sleep(45)
                continue
            if r.status_code != 200:
                time.sleep(10)
                continue
            mark_ok()
            rows = r.json()
            snap = {}
            for row in rows[1:]:
                ts, orig = row[1], row[2]
                n = norm(orig)
                if n not in snap or ts > snap[n]:
                    snap[n] = ts
            return snap
        except Exception as e:
            mark_fail()
            C.log("[s04m] CDX 异常 %s：%s" % (host, str(e)[:60]))
            time.sleep(20)
    return {}


def download_one(url, ts, tries=3):
    """下载一个快照的原始字节。返回 (ext, bytes) 或 None。"""
    for i in range(tries):
        rate_wait()
        try:
            r = sess().get("https://web.archive.org/web/%sid_/%s" % (ts, url),
                           timeout=90, stream=False)
            if r.status_code == 429:
                C.log("[s04m] 下载 429，全局退避 45s")
                mark_fail()
                time.sleep(45)
                continue
            if r.status_code != 200 or len(r.content) < MIN_BYTES:
                return None
            for magic, ext in MAGIC:
                if r.content[:len(magic)] == magic:
                    mark_ok()
                    return ext, r.content
            return None   # 非图（HTML/文本），丢弃
        except Exception:
            mark_fail()
            time.sleep(8)
    return None


def store(orig, data, ext, src, man):
    """按原始 URL 哈希落盘（保 manifest→s04d 映射），并更新 manifest。"""
    h = hashlib.sha1(orig.encode("utf-8")).hexdigest()
    d = os.path.join(C.IMAGES_DIR, h[:2])
    os.makedirs(d, exist_ok=True)
    fp = os.path.join(d, h[:20] + "." + ext)
    with open(fp, "wb") as f:
        f.write(data)
    ctype = {"jpg": "image/jpeg", "png": "image/png",
             "gif": "image/gif", "webp": "image/webp"}[ext]
    man[orig] = {"local": "images/%s/%s.%s" % (h[:2], h[:20], ext),
                 "bytes": len(data), "ctype": ctype, "ok": True, "src": src}


def try_origin(inner, tries=2):
    """原站下载内层 URL（先代理后直连，带域级 referer）。返回 (ext, bytes) 或 None。
    注：URL 死是常态，不计入 _net_fail_streak（否则触发全局冷却拖死整批）。"""
    from s04j_rescue import ref_for
    ref = ref_for(inner.split("/")[2])
    for i in range(tries):
        rate_wait()
        try:
            s = mk_session()
            if i > 0:
                s.proxies = {}   # 第二次直连
            r = s.get(inner, timeout=12, headers={"User-Agent": UA, "Referer": ref})
            if r.status_code == 200 and len(r.content) >= MIN_BYTES:
                for magic, ext in MAGIC:
                    if r.content[:len(magic)] == magic:
                        return ext, r.content
            if r.status_code == 200:
                return None   # 200 但非图
        except Exception:
            pass
    return None


def mutant_mode(man):
    """畸形双协议 URL 专项：内层重建 → 原站 → Wayback。"""
    import re as _re
    from s04j_rescue import collect_missing as cm2, inner_urls
    ms = cm2()
    mut = [u for u in ms if len(list(_re.finditer(r"https?://", u))) >= 2]
    C.log("[s04m] --mutant：畸形 URL %d 张" % len(mut))
    done, wb_need, failed = [], [], []
    from s04j_rescue import DEAD_HOSTS
    for i, u in enumerate(mut, 1):
        cands = inner_urls(u)
        got = None
        # 内层 host 全在死域名单 → 跳过原站，直接进 Wayback 队列
        if all(host_of(c).split(":")[0] in DEAD_HOSTS for c in cands):
            wb_need.append(cands[0])
        else:
            for cand in cands:
                got = try_origin(cand)
                if got:
                    break
            if got:
                store(u, got[1], got[0], "mutant_fix", man)
                done.append(u)
            else:
                wb_need.append(cands[0])   # 内层真实 URL 进 Wayback 队列
        if i % 20 == 0:
            C.log("[s04m] mutant %d/%d 原站成功%d" % (i, len(mut), len(done)))
    C.log("[s04m] mutant 原站救回 %d，余 %d 转 Wayback" % (len(done), len(wb_need)))

    # Wayback：按内层 host 分组查 CDX（正确域名）；封锁期探测，避免几小时无效退避
    wb_ok, wb_fail = [], []
    if wb_need:
        probe = download_one(wb_need[0], "2")   # ts=2 → 永远重定向到最新快照
        if probe is None and _net_fail_streak[0] >= 3:
            C.log("[s04m] Wayback 仍被封锁（探测 429），本批 %d 张留档待解封后重跑" % len(wb_need))
            C.write_json(MPATH, man)
            C.log("[s04m] --mutant 完成：原站救回 %d，Wayback 部分全部待重试；下一步解封后重跑 --mutant" % len(done))
            return 0
    byh = {}
    for orig, inner in zip([u for u in mut if u not in done], wb_need):
        byh.setdefault(host_of(inner), []).append((orig, inner))
    for host, pairs in byh.items():
        snap = cdx_query(host)
        for orig, inner in pairs:
            ts = snap.get(norm(inner))
            if not ts:
                failed.append(orig)
                continue
            got = download_one(inner, ts)
            if got:
                store(orig, got[1], got[0], "wayback", man)
                wb_ok.append(orig)
            else:
                failed.append(orig)
        C.log("[s04m] mutant-Wayback %-30s %d/%d" % (host, len(wb_ok), len(pairs)))
    C.write_json(MPATH, man)
    C.log("[s04m] --mutant 完成：原站 %d + Wayback %d = 共救回 %d / %d，失败 %d"
          % (len(done), len(wb_ok), len(done) + len(wb_ok), len(mut), len(failed)))
    C.log("[s04m] 下一步：s04f → s04d → s06")
    return 0


def main():
    C.guard_or_exit("s04m")          # 写图 + 整本覆写 manifest
    if C.new_images_only():
        # collect_missing() 的口径是「本机没有文件 = 未落地」，
        # 而历史图本来就在那一台电脑上 -> 会把 13.8 万张拉去 CDX 查档并重下。
        C.log("[s04m] 图片库模式=incremental：历史图在外部库，不跑 Wayback 抢救（挂库后再说）")
        print("S04M_SKIP_INCREMENTAL")
        return 0
    args = sys.argv[1:]
    scan_only = "--scan" in args
    retry_mode = "--retry" in args

    man0 = C.read_json(MPATH, {}) or {}
    if "--mutant" in args:
        return mutant_mode(man0)

    # 1) 未落地集合（s04j 同口径）
    from s04j_rescue import collect_missing
    missing = collect_missing()
    C.log("[s04m] 未落地 %d 张" % len(missing))
    by_host = {}
    for u in missing:
        by_host.setdefault(host_of(u), []).append(u)
    C.log("[s04m] %d 个域" % len(by_host))

    # 2) CDX 查档
    snaps = {}   # norm -> (orig_url, ts)
    _prev = resume_prev()
    resume = C.read_json(_prev, None) if _prev else None
    if resume and resume.get("snaps"):
        C.log("[s04m] 复用上次 CDX 查档结果（%d 命中）" % len(resume["snaps"]))
        for n, (ou, ts) in resume["snaps"].items():
            snaps[n] = (ou, ts)
    elif retry_mode and resume:
        C.log("[s04m] --retry：重试上次无档/失败 %d 张" % len(resume.get("no_snap", [])))
        targets = resume.get("no_snap", [])
    else:
        n = 0
        for host, urls in sorted(by_host.items(), key=lambda kv: -len(kv[1])):
            snap = cdx_query(host)
            hit = 0
            for u in urls:
                ts = snap.get(norm(u))
                if ts:
                    snaps[norm(u)] = (u, ts)
                    hit += 1
            n += 1
            C.log("[s04m] CDX %2d/%d %-32s 未落地%5d 快照%5d" %
                  (n, len(by_host), host, len(urls), hit))
        targets = [u for u, _ in snaps.values()]
        C.log("[s04m] 查档完成：%d/%d 有存档" % (len(targets), len(missing)))
        if scan_only:
            C.write_json(resume_out(), {"snaps": {k: list(v) for k, v in snaps.items()},
                                        "no_snap": sorted(set(missing) - set(snaps))})
            return 0

    if not targets:
        C.write_json(resume_out(), {"snaps": {}, "no_snap": sorted(set(missing))})
        C.log("[s04m] 无任何存档，结束")
        return 0

    # 3) 下载
    man = man0
    done, failed = [], []
    t0 = time.time()
    for i, u in enumerate(targets, 1):
        ts = snaps.get(norm(u), (None, None))[1]
        got = download_one(u, ts)
        if not got:
            failed.append(u)
            if i % 50 == 0:
                C.log("[s04m] 下载 %d/%d 成功%d 失败%d %.1fs" %
                      (i, len(targets), len(done), len(failed), time.time() - t0))
            continue
        ext, data = got
        h = hashlib.sha1(u.encode("utf-8")).hexdigest()
        d = os.path.join(C.IMAGES_DIR, h[:2])
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, h[:20] + "." + ext)
        with open(fp, "wb") as f:
            f.write(data)
        ctype = {"jpg": "image/jpeg", "png": "image/png",
                 "gif": "image/gif", "webp": "image/webp"}[ext]
        man[u] = {"local": "images/%s/%s.%s" % (h[:2], h[:20], ext),
                  "bytes": len(data), "ctype": ctype, "ok": True, "src": "wayback"}
        done.append(u)
        if i % 50 == 0:
            C.log("[s04m] 下载 %d/%d 成功%d 失败%d %.1fs" %
                  (i, len(targets), len(done), len(failed), time.time() - t0))

    # 4) 落盘 manifest + 明细
    C.write_json(MPATH, man)
    C.write_json(resume_out(), {"snaps": {k: list(v) for k, v in snaps.items()},
                          "saved": done, "failed": failed})
    C.log("[s04m] 完成：存档命中 %d | 下载成功 %d | 失败 %d | 用时 %.0fs"
          % (len(targets), len(done), len(failed), time.time() - t0))
    C.log("[s04m] 下一步：s04f → s04d → s06")
    return 0


if __name__ == "__main__":
    sys.exit(main())
