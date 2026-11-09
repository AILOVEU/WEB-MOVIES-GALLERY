# -*- coding: utf-8 -*-
"""预告片「扫描→统一下载→失败统一重试」三段式调度（针对整个 top250 全部榜单里尚未判定的番号）。

设计动机：Cloudflare 的 cf_clearance 只有约 30 分钟有效期，且代理节点偶发抖动。
所以先做**廉价扫描**（每条只探 1 次：preview_url + m3u8 的 HTTP 码），据此把番号分成：
  - has      : m3u8 返回 200 且有 #EXTM3U → 有预告片，进下载队列（ckpt 状态 queued）
  - no       : m3u8 返回 404 → 确认无预告片（终态）→ 记 videos_no_preview.json，今后不再扫
  - probe_fail: 拿不到 url（cookie/CF/网络问题）或 m3u8 非 200/404 → **失败一次就跳过**，
               不当终态、记 scan_fail，留待“之后统一尝试”
扫描完再对 queued 的番号**统一下载**；下载失败的记 dl_fail，也归入“之后统一重试”。

用法（需 _pipeline/javdb_cookie.txt 为当前代理 IP 下的有效 cookie）：
  python trailers_sweep.py --phase all          # 扫描全部未判定 → 再下载有预告的
  python trailers_sweep.py --phase scan         # 只扫描
  python trailers_sweep.py --phase download      # 只下载已扫描出的 queued
  python trailers_sweep.py --retry-failed        # 把之前的 scan_fail/dl_fail 也纳入重扫/重试
  python trailers_sweep.py --workers 3
判定/去重复用 videos_manifest.json(已下/迁移) + videos_no_preview.json(确认无) + trailers_ckpt.json(状态)。
"""
# __JDB_BOOT__ 统一 ROOT/sys.path 引导：脚本可从 _script 任意子目录运行、跨目录彼此 import
import os as _os, sys as _sys
ROOT = _os.path.dirname(_os.path.abspath(__file__))
while not _os.path.isdir(_os.path.join(ROOT, "data")) and ROOT != _os.path.dirname(ROOT):
    ROOT = _os.path.dirname(ROOT)
SR = _os.path.join(ROOT, "_script")
for _d in [SR] + [_os.path.join(SR, _x) for _x in _os.listdir(SR)
               if _os.path.isdir(_os.path.join(SR, _x)) and _x not in ("legacy", "__pycache__") and not _x.startswith(".")]:
    if _d not in _sys.path:
        _sys.path.insert(0, _d)
# __JDB_BOOT_END__
import argparse
import concurrent.futures as cf
import json
import os
import sys
import threading
import time

import win_utf8        # noqa: F401  重定向/管道下固定 UTF-8，否则 ✅ 会砸死进程
import trailers_download as T   # 复用 download_one / curl / get_m3u8_url / loadjs / NPV 助手 / 常量
import videos_manifest as vm

_lock = threading.Lock()


def all_targets():
    """整个 top250 所有 variant 的番号并集（含 all/0/1/2/3/各年份），去重，带 vid。"""
    variants = T.loadjs("top250.js")["top250"]["variants"]
    movies = T.loadjs("movies.js")
    out, seen = [], set()
    for v in variants.values():
        for it in v.get("items", []):
            c = it["code"]
            if c.lower() in seen:
                continue
            seen.add(c.lower())
            out.append((c, (movies.get(c) or {}).get("id")))
    return out


def classify(code, man, ckpt, npv_low):
    """返回该番号当前状态：downloaded / no / queued / failed / unknown。"""
    if vm.match(code, man)[0] or vm.match(T.out_name(code), man)[0]:
        return "downloaded"
    st = ckpt.get(code, {}).get("status", "")
    base = st.split(":")[0]
    if base in ("ok", "exists"):
        return "downloaded"
    if base == "nopreview" or code.lower() in npv_low:
        return "no"
    if base == "queued":
        return "queued"
    if base in ("scan_fail", "dl_fail", "key_fail", "seg_fail", "ffmpeg_fail",
                "m3u8_fail", "nokey", "noseg", "exc"):
        return "failed"
    return "unknown"


def set_ckpt(ckpt, code, status):
    with _lock:
        ckpt[code] = {"status": status, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


def probe(code, vid, cookie, delay=0.0):
    """单条扫描：只 1 次。返回 has / no / probe_fail。delay>0 先睡一下限速，降低 CF 限流。"""
    if delay:
        time.sleep(delay)
    if not vid:
        return "no"  # 没有 javdb 内部 id，无法有预告片
    url = T.get_m3u8_url(vid, cookie)
    if not url:
        return "probe_fail"          # cookie/CF/网络导致拿不到 url
    tmp = os.path.join(T.slot_dir(T.TMP), "probe.m3u8")   # 写进自己那份 slot，固定名反复覆盖（旧版每 vid 一个文件，积到几百个）
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    _, code_http = T.curl(url, tmp, timeout=30, cookie=cookie)
    body = ""
    if os.path.exists(tmp):
        try:
            body = open(tmp, encoding="utf-8", errors="ignore").read()
        except Exception:
            body = ""
    if code_http == "404":
        return "no"                  # 确认无预告片（终态）
    if code_http == "200" and "#EXTM3U" in body:
        return "has"
    return "probe_fail"              # 非预期：跳过，之后统一重试


def do_scan(targets, cookie, workers, npv, ckpt, retry_failed, delay=0.0, limit=0):
    man = vm.load()
    npv_low = {c.lower() for c in npv}
    todo = []
    for code, vid in targets:
        cls = classify(code, man, ckpt, npv_low)
        if cls == "unknown" or (retry_failed and cls == "failed"):
            todo.append((code, vid))
    if limit and limit > 0:
        todo = todo[:limit]
    print("[scan] 待扫描(未判定) %d 条；workers=%d delay=%.2fs limit=%d" %
          (len(todo), workers, delay, limit), flush=True)
    if not todo:
        return {"has": 0, "no": 0, "fail": 0}
    os.makedirs(T.TMP, exist_ok=True)
    T.set_slot_cap(workers)        # 扫描也要写 hlstmp：槽位数 = 并发数，不随重启累积
    res = {"has": 0, "no": 0, "fail": 0}
    done = 0
    CHUNK = max(workers * 15, 45)
    stopped = False
    for i0 in range(0, len(todo), CHUNK):
        batch = todo[i0:i0 + CHUNK]
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(probe, c, v, cookie, delay): (c, v) for c, v in batch}
            for fu in cf.as_completed(futs):
                code, vid = futs[fu]
                st = fu.result()
                done += 1
                if st == "has":
                    set_ckpt(ckpt, code, "queued"); res["has"] += 1
                elif st == "no":
                    T.record_npv(npv, code, vid); set_ckpt(ckpt, code, "nopreview"); res["no"] += 1
                else:
                    set_ckpt(ckpt, code, "scan_fail"); res["fail"] += 1
                T.save_ckpt(ckpt)
        print("[scan] 进度 %d/%d  has=%d no=%d fail=%d" %
              (done, len(todo), res["has"], res["no"], res["fail"]), flush=True)
        # 成功率熳断：探测足够样本后，命中(has+no)率 < 25% 则 CF 在限流，早停避免乱标
        if done >= 80:
            rate = (res["has"] + res["no"]) / done
            if rate < 0.25:
                print("[scan] !! 命中率仅 %.0f%%（has+no/探测数），CF 在限流。早停，剩余未扫描。"
                      "建议：提高 --delay、降 --workers，或换个更稳的节点，稍后 --phase scan --retry-failed 重试。"
                      % (rate * 100), flush=True)
                stopped = True
                break
    with _lock:
        T.save_npv(npv)
    print("[scan] 完成：有预告(queued) %d / 确认无 %d / 失败待重试 %d%s" %
          (res["has"], res["no"], res["fail"], "（提前熳断）" if stopped else ""), flush=True)
    return res


def do_download(cookie, workers, ckpt, retry_failed):
    man = vm.load()
    targets = all_targets()
    vidmap = dict(targets)
    pend = []
    for code in ckpt:
        base = ckpt[code].get("status", "").split(":")[0]
        if base == "queued" or (retry_failed and base in ("dl_fail", "seg_fail", "key_fail",
                              "ffmpeg_fail", "m3u8_fail")):
            if vm.match(code, man)[0] or vm.match(T.out_name(code), man)[0]:
                continue  # 其实已下载（迁移也算），跳过
            pend.append((code, vidmap.get(code)))
    print("[download] 待下载(已扫描出有预告) %d 条；workers=%d" % (len(pend), workers), flush=True)
    if not pend:
        return {"ok": 0, "fail": 0}
    os.makedirs(T.VID_DIR, exist_ok=True)
    os.makedirs(T.TMP, exist_ok=True)
    T.set_slot_cap(workers)        # 下载槽位数 = 并发数
    ok = fail = done = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(T.worker, (c, v), cookie, T.TMP, 3, 2): (c, v) for c, v in pend}
        for fu in cf.as_completed(futs):
            code, st, size, dt = fu.result()
            done += 1
            base = st.split(":")[0]
            if base in ("ok", "exists"):
                ok += 1
            else:
                st = "dl_fail"          # 统一归为下载失败，之后 --retry-failed 重试
                fail += 1
            set_ckpt(ckpt, code, st)
            T.save_ckpt(ckpt)
            if done % 20 == 0:
                print("[download] %d/%d  ok=%d fail=%d" % (done, len(pend), ok, fail), flush=True)
    print("[download] 登记清单 ...", flush=True)
    import subprocess as sp
    sp.run([sys.executable, os.path.join(SR, "videos_manifest.py"), "scan"],
           capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("[download] 完成：成功 %d / 失败(待重试) %d" % (ok, fail), flush=True)
    return {"ok": ok, "fail": fail}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["all", "scan", "download"])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--retry-failed", action="store_true",
                    help="把之前 scan_fail/dl_fail 等失败项也重新纳入扫描/下载")
    ap.add_argument("--delay", type=float, default=0.3,
                    help="扫描时每条探测前的限速秒数（默认 0.3，降低 CF 限流；被限流就调大）")
    ap.add_argument("--limit", type=int, default=0,
                    help="本次扫描最多只处理前 N 条（0=全部），用于小批试水")
    args = ap.parse_args()

    cookie = open(T.COOKIE, encoding="utf-8").read().strip()
    # 自检：代理 + cookie
    _, code = T.curl("https://javdb.com", None, timeout=20, cookie=cookie)
    # T.curl 无 dest 时 body 进 stdout，code 取末3位
    if code != "200":
        print("[sweep] 代理不通或 cookie 失效（javdb.com 返回 %r）。请先重抓 cookie。" % code, flush=True)
        return 2

    ckpt = T.load_ckpt()
    npv = T.load_npv()
    targets = all_targets()

    if args.phase in ("all", "scan"):
        do_scan(targets, cookie, args.workers, npv, ckpt, args.retry_failed,
                delay=args.delay, limit=args.limit)
    if args.phase in ("all", "download"):
        do_download(cookie, args.workers, ckpt, args.retry_failed)

    # 汇总
    import collections
    cnt = collections.Counter(v["status"].split(":")[0] for v in ckpt.values())
    print("[sweep] 当前 ckpt 状态分布：%s" % dict(cnt), flush=True)
    print("[sweep] 无预告记录 %d；清单记录 %d" %
          (len(npv), len(vm.load()["items"])), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
