# -*- coding: utf-8 -*-
"""TOP250 预告片下载器（DMM 真实地址版）。

为什么重写（2026-11-25）：
  旧 trailers_download.py 走 javdb 的 /v/{vid}/preview_url，javdb 现在连 top250 都要登录，
  cookie 三天失效。而且实测发现 DMM 的真实预告片地址是
      https://tencc3001.dmm.co.jp/pv/<签名token>/<cid>hhb.mp4
  带随机 token，**无法用规则构造**，必须从页面 HTML 里提取。
  curl 抓 video.dmm.co.jp 一律返回 32287 字节拦截页，只有真实浏览器能拿到。

本脚本：
  1) 只处理 top250 的 250 个番号（不碰全库）
  2) 跳过：videos_manifest.json 已记录的（含已迁移）、videos_no_preview.json 确认无预告的
  3) 用 CDP 浏览器逐个打开 DMM 页面，提取 /pv/ 地址
  4) 下载到 assets/videos/<code>.mp4（命名与旧脚本一致）
  5) 登记进 videos_manifest.json（与旧脚本同一套记录格式）

用法：
  python top250_trailers_dmm.py --dry-run          # 只看要处理多少
  python top250_trailers_dmm.py --limit 10         # 只跑前 10 个（试跑）
  python top250_trailers_dmm.py                    # 全量
  python top250_trailers_dmm.py --recheck          # 连「确认无预告」的也重查
"""
# __JDB_BOOT__
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
import base64
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

import win_utf8        # noqa: F401

SITE = os.path.join(ROOT, "data", "site")
STATE = os.path.join(ROOT, "data", "state")
ASSETS = os.path.join(ROOT, "assets")
VID_DIR = os.path.join(ASSETS, "videos")
WB = os.path.join(ROOT, "_pipeline")
LOGS = os.path.join(ROOT, "_logs")
CKPT = os.path.join(WB, "top250_dmm_ckpt.json")

CDP = "http://127.0.0.1:9222"
PROXY = "http://127.0.0.1:7890"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

PV_RE = re.compile(r'https?://tencc3001\.dmm\.co\.jp/pv/[A-Za-z0-9_\-]+/[A-Za-z0-9_]+\.mp4')
CID_RE = re.compile(r"/digital/video/([a-z0-9_]+)/")


def code_to_cid(code):
    """番号 -> DMM cid 兜底推算（没有剧照地址时用）。

    DMM cid 规则：厂牌小写 + 数字补到 5 位。
      IPX-811   -> ipx00811
      STARS-804 -> stars00804
      ABF-017   -> abf00017
      MIDA-234  -> mida00234
      LAFBD-41  -> lafbd00041
    带下划线/特殊后缀的（如 010115_001）不适用，返回 None 让调用方跳过。
    """
    s = (code or "").strip().upper()
    m = re.match(r"^([A-Z]+)-(\d+)$", s)
    if not m:
        return None
    brand, num = m.group(1).lower(), m.group(2)
    return brand + num.zfill(5)

LOG = os.path.join(LOGS, time.strftime("%Y-%m-%d") + "_top250_dmm.log")


def log(*a):
    line = "%s %s" % (time.strftime("[%H:%M:%S]"), " ".join(str(x) for x in a))
    print(line, flush=True)
    try:
        os.makedirs(LOGS, exist_ok=True)
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------- 数据加载 ----------------
def loadjs(name):
    raw = io.open(os.path.join(SITE, name), encoding="utf-8").read()
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])


def load_ckpt():
    if not os.path.exists(CKPT):
        return {}
    try:
        return json.load(io.open(CKPT, encoding="utf-8")) or {}
    except Exception:
        return {}


def save_ckpt(d):
    os.makedirs(WB, exist_ok=True)
    io.open(CKPT, "w", encoding="utf-8").write(
        json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True))


def out_name(code):
    """与旧脚本一致：<code>.mp4（斜杠换成下划线）。"""
    return code.replace("/", "_") + ".mp4"


# ---------------- CDP 极简客户端 ----------------
class CDPWS:
    def __init__(self, url):
        m = re.match(r"ws://([^:/]+):(\d+)(/.*)", url)
        self.host, self.port, self.path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = None
        self._id = 0
        self._buf = b""

    def __enter__(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n" % (self.path, self.host, self.port, key))
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        return self

    def __exit__(self, *a):
        try:
            self.sock.close()
        except Exception:
            pass

    def call(self, method, params=None, timeout=25):
        self._id += 1
        want = self._id
        payload = json.dumps({"id": want, "method": method, "params": params or {}}).encode()
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            hdr = bytes([0x81, 0x80 | n])
        elif n < 65536:
            hdr = bytes([0x81, 0x80 | 126]) + n.to_bytes(2, "big")
        else:
            hdr = bytes([0x81, 0x80 | 127]) + n.to_bytes(8, "big")
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

        self.sock.settimeout(2)
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                continue
            except Exception:
                break
            if not chunk:
                break
            self._buf += chunk
            while len(self._buf) >= 2:
                b1 = self._buf[1]
                ln = b1 & 0x7F
                off = 2
                if ln == 126:
                    if len(self._buf) < 4:
                        break
                    ln = int.from_bytes(self._buf[2:4], "big")
                    off = 4
                elif ln == 127:
                    if len(self._buf) < 10:
                        break
                    ln = int.from_bytes(self._buf[2:10], "big")
                    off = 10
                if len(self._buf) < off + ln:
                    break
                body = self._buf[off:off + ln]
                self._buf = self._buf[off + ln:]
                try:
                    msg = json.loads(body.decode("utf-8", "ignore"))
                except Exception:
                    continue
                if msg.get("id") == want:
                    return msg.get("result", {})
        return {}


def cdp_page():
    with urllib.request.urlopen(CDP + "/json", timeout=10) as r:
        tabs = json.loads(r.read().decode("utf-8"))
    return next((t for t in tabs if t.get("type") == "page"), None)


def probe_dmm(cid, wait=3.2):
    """打开 DMM 页面，返回 (status, pv_url)。status: OK / 404 / NO_PV / ERR"""
    pg = cdp_page()
    if not pg:
        return "ERR", None
    try:
        with CDPWS(pg["webSocketDebuggerUrl"]) as ws:
            ws.call("Page.navigate", {"url": "https://video.dmm.co.jp/av/content/?id=%s" % cid})
            time.sleep(wait)
            r1 = ws.call("Runtime.evaluate", {
                "expression": "document.documentElement.innerHTML", "returnByValue": True})
            html = (r1.get("result") or {}).get("value") or ""
            r2 = ws.call("Runtime.evaluate", {
                "expression": "document.body?document.body.innerText.slice(0,400):''",
                "returnByValue": True})
            text = (r2.get("result") or {}).get("value") or ""
    except Exception as e:
        log("  CDP 异常:", e)
        return "ERR", None

    if "404" in text or "見つかりません" in text or "Not Found" in text:
        return "404", None
    m = PV_RE.search(html)
    if m:
        return "OK", m.group(0)
    return "NO_PV", None


# ---------------- 下载 ----------------
def download(url, dest, timeout=600):
    """直接写目标文件（沙盒禁 tmp+replace）。返回 (ok, size)。"""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    cmd = ["curl", "-sL", "--ssl-no-revoke", "--max-time", str(timeout),
           "-A", UA, "-x", PROXY, "-H", "Referer: https://video.dmm.co.jp/",
           "-o", dest, "-w", "%{http_code}", url]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    code = (r.stdout or "").strip()[-3:]
    size = os.path.getsize(dest) if os.path.exists(dest) else 0
    if code == "200" and size > 100 * 1024:
        return True, size
    if os.path.exists(dest):
        try:
            os.remove(dest)
        except OSError:
            pass
    return False, 0


def main():
    ap = argparse.ArgumentParser(description="TOP250 预告片下载（DMM 真实地址）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个（0=全部）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recheck", action="store_true", help="连确认无预告的也重查")
    ap.add_argument("--wait", type=float, default=3.2, help="每页等待渲染秒数")
    args = ap.parse_args()

    # 1) 取 top250 番号
    ranks = loadjs("top250.js")["top250"]["variants"]
    movies = loadjs("movies.js")
    samples = {}
    smp_path = os.path.join(STATE, "samples.js")
    if os.path.exists(smp_path):
        t = io.open(smp_path, encoding="utf-8").read()
        i = t.index("window.JAVDB_SAMPLES") + len("window.JAVDB_SAMPLES")
        rest = t[i:].lstrip()
        if rest.startswith("="):
            rest = rest[1:].lstrip()
        samples, _ = json.JSONDecoder().raw_decode(rest)

    codes, seen = [], set()
    for it in ranks.get("all", {}).get("items", []):
        c = it["code"]
        if c in seen:
            continue
        seen.add(c)
        codes.append(c)

    # 2) 去重：已下载 / 已确认无预告
    man = json.load(io.open(os.path.join(STATE, "videos_manifest.json"), encoding="utf-8"))
    man_items = man.get("items", {})
    npv = json.load(io.open(os.path.join(STATE, "videos_no_preview.json"), encoding="utf-8"))
    npv_items = npv.get("items", {})
    ckpt = load_ckpt()

    todo, skip_dl, skip_npv, no_cid = [], 0, 0, 0
    for c in codes:
        fn = out_name(c)
        if fn in man_items or c in man_items:
            skip_dl += 1
            continue
        st = ckpt.get(c, {}).get("status", "")
        if st == "ok":
            skip_dl += 1
            continue
        if c in npv_items and not args.recheck:
            skip_npv += 1
            continue
        # 找 cid：优先从剧照地址反推（最准），没有就用番号规则推算
        cid = None
        for u in (samples.get(c) or []):
            m = CID_RE.search(u)
            if m:
                cid = m.group(1)
                break
        if not cid:
            cid = code_to_cid(c)
        if not cid:
            no_cid += 1
            continue
        todo.append((c, cid))

    if args.limit:
        todo = todo[:args.limit]

    log("TOP250 共 %d 部 | 已下载 %d | 确认无预告 %d | 无 cid %d | 本轮待处理 %d%s"
        % (len(codes), skip_dl, skip_npv, no_cid, len(todo),
           "  [--recheck]" if args.recheck else ""))

    if args.dry_run:
        for c, cid in todo[:20]:
            print("  %-14s cid=%s" % (c, cid))
        log("dry-run，未下载。")
        return 0
    if not todo:
        log("没有需要处理的条目。")
        return 0

    # 3) 逐个探测 + 下载
    ok = fail = nopv = 0
    t0 = time.time()
    for i, (code, cid) in enumerate(todo, 1):
        st, pv = probe_dmm(cid, wait=args.wait)
        if st == "OK" and pv:
            dest = os.path.join(VID_DIR, out_name(code))
            good, size = download(pv, dest)
            if good:
                ok += 1
                ckpt[code] = {"status": "ok", "size": size, "url": pv,
                              "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                log("[%d/%d] %-14s OK  %.1f MB" % (i, len(todo), code, size / 1048576))
            else:
                fail += 1
                ckpt[code] = {"status": "dl_fail", "url": pv,
                              "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
                log("[%d/%d] %-14s 下载失败" % (i, len(todo), code))
        elif st == "404":
            nopv += 1
            ckpt[code] = {"status": "404", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            log("[%d/%d] %-14s DMM 404（确认无此片）" % (i, len(todo), code))
        elif st == "NO_PV":
            nopv += 1
            ckpt[code] = {"status": "no_pv", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            log("[%d/%d] %-14s 页面无 /pv/ 地址" % (i, len(todo), code))
        else:
            fail += 1
            ckpt[code] = {"status": "probe_fail", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            log("[%d/%d] %-14s 探测失败" % (i, len(todo), code))
        save_ckpt(ckpt)

    log("DONE 成功 %d 失败 %d 无预告 %d | 用时 %.0f 分钟"
        % (ok, fail, nopv, (time.time() - t0) / 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
