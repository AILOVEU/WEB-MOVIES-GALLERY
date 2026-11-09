# -*- coding: utf-8 -*-
"""Re-probe errored entries from previews_errors.json (sequential, gentle)."""
import json, io, os, subprocess, time, re

WS = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17"
WB = os.path.join(WS, ".workbuddy")
CK = io.open(os.path.join(WB, "javdb_cookie.txt"), encoding="utf-8").read().strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

errs = json.load(io.open(os.path.join(WB, "previews_errors.json"), encoding="utf-8"))
prev = json.load(io.open(os.path.join(WB, "previews.json"), encoding="utf-8"))
print("errors to re-probe:", len(errs), flush=True)

recovered, still_err = [], []
for i, v in enumerate(errs):
    ok = False
    for attempt in range(4):
        r = subprocess.run(["curl", "-s", "--ssl-no-revoke", "--max-time", "20",
                            "-x", "http://127.0.0.1:7890", "-A", UA,
                            "-H", f"Cookie: {CK}", "-H", "Referer: https://javdb.com/",
                            "-w", "\n%{http_code}",
                            f"https://javdb.com/v/{v['id']}/preview_url"],
                           capture_output=True, text=True)
        out = r.stdout.strip()
        body, _, code = out.rpartition("\n")
        if code == "200":
            m = re.search(r'"url"\s*:\s*"([^"]+)"', body)
            if m:
                u = m.group(1).replace("\\u0026", "&")
                v["url"] = u if u.startswith("http") else "https://javdb.com" + u
                recovered.append(v)
                ok = True
            break
        if code == "404":
            ok = True  # genuinely no preview
            break
        time.sleep(2 * (attempt + 1))
    if not ok:
        still_err.append(v)
    if (i + 1) % 30 == 0:
        print(f"re-probed {i+1}/{len(errs)}, recovered {len(recovered)}", flush=True)
    time.sleep(0.8)

final = prev + recovered
io.open(os.path.join(WB, "previews.json"), "w", encoding="utf-8").write(json.dumps(final, ensure_ascii=False, indent=1))
io.open(os.path.join(WB, "previews_errors.json"), "w", encoding="utf-8").write(json.dumps(still_err, ensure_ascii=False, indent=1))
print(f"recovered {len(recovered)}, still error {len(still_err)}, total previews {len(final)}")
