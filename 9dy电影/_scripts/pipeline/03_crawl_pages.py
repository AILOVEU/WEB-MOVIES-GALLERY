# -*- coding: utf-8 -*-
"""遍历 9dy.top 影片详情页, 原始 HTML 存盘 (断点续传)。

用法:
  python _scripts/pipeline/03_crawl_pages.py                     # 全部 (_raw_posts.json 的 id, 已存在跳过)
  python _scripts/pipeline/03_crawl_pages.py --ids=1,2,3         # 只抓指定 id
  python _scripts/pipeline/03_crawl_pages.py --limit=3           # 只抓前 N 个待抓页 (试跑)
  python _scripts/pipeline/03_crawl_pages.py --workers=3         # 并发 (默认 3, 防爬限流)
  python _scripts/pipeline/03_crawl_pages.py --dir=<画廊目录>
输出: <dir>/_rawdata/pages/{id}.html；失败清单写 <dir>/_logs/<今日>_crawl_errors.txt（日志名强制带日期前缀）
"""
import json, os, sys, time, random
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> _scripts/（common.py 所在）
from common import gallery_dir, pages_dir, meta_file, log_file, parse_ids_arg, arg_int

GDIR = gallery_dir()
OUT = pages_dir(GDIR)
RAW = meta_file(GDIR, "_raw_posts.json")
os.makedirs(OUT, exist_ok=True)

with open(RAW, encoding="utf-8") as f:
    items = json.load(f)["items"]
ids = [it["id"] for it in items]

only = parse_ids_arg()
if only:
    ids = [i for i in ids if i in set(only)]
todo = [i for i in ids if not os.path.exists(os.path.join(OUT, str(i) + ".html"))]
limit = arg_int("--limit=", None, "--limit")
if limit is not None:
    todo = todo[:limit]
print("total:", len(ids), "todo:", len(todo), flush=True)


def fetch(pid):
    url = "https://www.9dy.top/%d.html" % pid
    path = os.path.join(OUT, str(pid) + ".html")
    last_err = None
    for attempt in range(3):
        try:
            from common import get
            text, err = get(url, headers={
                "Accept": "text/html,application/xhtml+xml"}, tries=1)
            if err:
                raise RuntimeError(err)
            if 'id="info"' not in text and len(text) < 20000:
                raise RuntimeError("page looks invalid (%d bytes)" % len(text))
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return pid, len(text), None
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1) + random.random())
    return pid, 0, str(last_err)


t0 = time.time()
ok = fail = 0
errs = []
workers = arg_int("--workers=", None, "--workers") or int(os.environ.get("WORKERS", "3"))
with ThreadPoolExecutor(max_workers=workers) as ex:
    futs = {ex.submit(fetch, pid): pid for pid in todo}
    for n, fut in enumerate(as_completed(futs), 1):
        pid, size, err = fut.result()
        if err:
            fail += 1
            errs.append((pid, err))
            print("FAIL %d: %s" % (pid, err), flush=True)
        else:
            ok += 1
        if n % 100 == 0:
            rate = n / (time.time() - t0)
            eta = (len(todo) - n) / max(rate, 0.1)
            print("progress %d/%d ok=%d fail=%d %.1f p/s ETA %.0fs"
                  % (n, len(todo), ok, fail, rate, eta), flush=True)

print("DONE ok=%d fail=%d elapsed=%.0fs" % (ok, fail, time.time() - t0), flush=True)
if errs:
    err_path = log_file(GDIR, "crawl_errors.txt")
    with open(err_path, "w", encoding="utf-8") as f:
        for pid, e in errs:
            f.write("%d\t%s\n" % (pid, e))
    print("errors written to %s" % err_path, flush=True)
