# -*- coding: utf-8 -*-
"""Download one javdb preview video (HLS/AES-128) to mp4 via curl + local ffmpeg."""
import json, os, re, subprocess, sys, time

WS = r"C:\Users\wh\WorkBuddy\2026-10-10-07-52-17"
WB = os.path.join(WS, ".workbuddy")
VID = os.path.join(WS, "javdb_videos")
TMP = os.path.join(WB, "hlstmp")
CK = open(os.path.join(WB, "javdb_cookie.txt"), encoding="utf-8").read().strip()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"
os.makedirs(VID, exist_ok=True)
os.makedirs(TMP, exist_ok=True)

def curl(url, dest=None, timeout=60):
    cmd = ["curl", "-sL", "--ssl-no-revoke", "--max-time", str(timeout),
           "-x", "http://127.0.0.1:7890", "-A", UA, "-H", f"Cookie: {CK}", url]
    if dest:
        cmd += ["-o", dest]
        return subprocess.run(cmd, capture_output=True, text=True)
    return subprocess.run(cmd, capture_output=True, text=True).stdout

def get_preview_url(vid):
    r = curl(f"https://javdb.com/v/{vid}/preview_url")
    if not r.strip():
        return None
    try:
        j = json.loads(r)
    except Exception:
        return None
    u = j.get("url", "")
    if not u:
        return None
    return u if u.startswith("http") else "https://javdb.com" + u

def download(vid, code, out_path):
    m3u8_url = get_preview_url(vid)
    if not m3u8_url:
        return "nopreview"
    # 固定文件名复用（覆盖写），不做任何删除 —— 避免触发批量删除安全钩子
    r = curl(m3u8_url, os.path.join(TMP, "index.m3u8"))
    m3u8_path = os.path.join(TMP, "index.m3u8")
    if r.returncode != 0 or not os.path.exists(m3u8_path) or os.path.getsize(m3u8_path) < 50:
        return "m3u8_fail"
    body = open(m3u8_path, encoding="utf-8", errors="ignore").read()
    if "#EXTM3U" not in body:
        return "m3u8_fail"
    # key
    km = re.search(r'URI="([^"]+)"', body)
    if not km:
        return "nokey"
    if curl(km.group(1), os.path.join(TMP, "key.key")).returncode != 0:
        return "key_fail"
    # segments
    segs = [l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")]
    local_lines = []
    for i, s in enumerate(segs):
        dest = os.path.join(TMP, f"seg{i:04d}.ts")
        ok = False
        for attempt in range(3):
            rr = curl(s, dest, timeout=90)
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                ok = True
                break
            time.sleep(1.5)
        if not ok:
            return f"seg_fail:{i}"
        local_lines.append(f"seg{i:04d}.ts")
    # rewrite local m3u8
    out = ["#EXTM3U", '#EXT-X-KEY:METHOD=AES-128,URI="key.key"', "#EXT-X-VERSION:3",
           "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-TARGETDURATION:10"]
    for l in local_lines:
        out.append("#EXTINF:10.0,")
        out.append(l)
    out.append("#EXT-X-ENDLIST")
    open(os.path.join(TMP, "local.m3u8"), "w", encoding="utf-8").write("\n".join(out))
    # ffmpeg decrypt + remux
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-protocol_whitelist", "file,crypto,data", "-allowed_extensions", "ALL",
                        "-i", os.path.join(TMP, "local.m3u8"),
                        "-c", "copy", "-bsf:a", "aac_adtstoasc", out_path],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 10000:
        return "ffmpeg_fail:" + r.stderr[-200:]
    return "ok"

if __name__ == "__main__":
    vid, code = sys.argv[1], sys.argv[2]
    out_path = os.path.join(VID, code.replace("/", "_") + ".mp4")
    if os.path.exists(out_path) and os.path.getsize(out_path) > 50000:
        print("exists")
        sys.exit(0)
    t0 = time.time()
    st = download(vid, code, out_path)
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    print(f"{code}: {st} size={size/1048576:.1f}MB time={time.time()-t0:.0f}s")
