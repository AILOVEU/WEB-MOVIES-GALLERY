# -*- coding: utf-8 -*-
"""下载影片剧照到 <dir>/assets/screenshots/{id}_{n}.{ext} (断点续传)

读取 <dir>/_rawdata/details.json 的 shots 字段; 文件已存在且 >1KB 则跳过。
用法:
  python _scripts/pipeline/05_download_shots.py                # 全量
  python _scripts/pipeline/05_download_shots.py --ids=1,2,3    # 只下指定影片的剧照
  python _scripts/pipeline/05_download_shots.py --dir=<画廊目录>
"""
import json, os, ssl, sys, time, random, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> _scripts/（common.py 所在）
from common import gallery_dir, meta_file, shots_dir, parse_ids_arg, UA, CTX

GDIR = gallery_dir()
DETAILS = meta_file(GDIR, "details.json")
OUT = shots_dir(GDIR)
os.makedirs(OUT, exist_ok=True)

with open(DETAILS, encoding="utf-8") as f:
    details = json.load(f)

only = parse_ids_arg()
if only:
    only = {str(i) for i in only}
    details = {k: v for k, v in details.items() if k in only}

jobs = []  # (url, path)
for pid, v in details.items():
    for n, url in enumerate(v.get("shots", []), 1):
        ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
        path = os.path.join(OUT, "%s_%d%s" % (pid, n, ext))
        if os.path.exists(path) and os.path.getsize(path) > 1024:
            continue
        # URL 可能含中文, 需 percent-encode
        from urllib.parse import quote, urlsplit, urlunsplit
        sp = urlsplit(url)
        url_enc = urlunsplit((sp.scheme, sp.netloc,
                              quote(sp.path), quote(sp.query, safe="=&"), ""))
        jobs.append((url_enc, path))

print("shots to download:", len(jobs), flush=True)


def fetch(job):
    url, path = job
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://www.9dy.top/"})
            with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                data = r.read()
            if len(data) < 1024:
                raise RuntimeError("too small: %d" % len(data))
            with open(path, "wb") as f:
                f.write(data)
            return True, None
        except Exception as e:
            last = e
            time.sleep(1 + attempt + random.random())
    return False, str(last)


t0 = time.time()
ok = fail = 0
errs = []
with ThreadPoolExecutor(max_workers=8) as ex:
    futs = [ex.submit(fetch, j) for j in jobs]
    for n, fut in enumerate(as_completed(futs), 1):
        good, err = fut.result()
        if good:
            ok += 1
        else:
            fail += 1
            errs.append(err)
        if n % 100 == 0:
            print("progress %d/%d ok=%d fail=%d" % (n, len(jobs), ok, fail), flush=True)

print("DONE ok=%d fail=%d elapsed=%.0fs" % (ok, fail, time.time() - t0), flush=True)
for e in errs[:20]:
    print("ERR:", e, flush=True)
