# -*- coding: utf-8 -*-
"""TOP250 总榜预告片批量下载器。

范围：top250.html 默认「全部」榜（top250.js -> variants['all']）里的 250 个番号。
数据：番号 -> javdb 内部 id（vid），从 movies.js 取；预告片走 /v/{vid}/preview_url。
落盘：assets/videos/{CODE}.mp4（HLS/AES-128，curl 抓 m3u8+key+ts，ffmpeg 封装）。
去重（★★以清单为权威判定，不看目录有无文件★★）：
      下载前先比对 data/state/videos_manifest.json —— 只要清单里有这条记录就算「已下载」，
      即使 assets/videos/ 目录为空 / 文件不在也算已下载（文件很可能已被手动搬到
      移动硬盘/别处，记录仍在就不重下，避免重复下载）。判定走 videos_manifest.match()，
      它同时按文件名与 code 匹配（present=false 的记录同样命中）。规则详见 RESUME.md 第 5.5 节。
无预告（★与“已下载/已迁移”分开记★）：m3u8 返回 404 = 真无预告且以后也不会有，写入独立持久记录
      项目 data/state/videos_no_preview.json（不进 videos_manifest.json，以免与“文件被搬走”混淆）。
      以后重跑遇到这些番号会**直接跳过、不再解析那个网页**；除非传 --refetch-no-preview 强制重取。
并发：默认 4 路。断点：trailers_ckpt.json 记录 ok/nopreview/fail，重跑自动跳过。
安全：只写目标文件与 assets/videos/，绝不删除 assets/videos/ 内容；代理 127.0.0.1:7890。

用法：
  python trailers_download.py                 # 下载 TOP250 总榜缺失的预告片
  python trailers_download.py --workers 4
  python trailers_download.py --dry-run       # 只打印将要下载的清单，不下载
  python trailers_download.py --retries 2      # 每个片段/整片重试次数
  python trailers_download.py --refetch-no-preview   # 忽略“无预告”记录，强制重新尝试这些番号
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
import random
import re
import subprocess
import sys
import threading
import time

import win_utf8        # noqa: F401  重定向/管道下固定 UTF-8，否则 ✅ 会砸死进程
DATA = os.path.join(ROOT, "data")                        # 站点数据与名单
SITE = os.path.join(DATA, "site")    # 页面要读的 *.js（HTML 写死引用）
STATE = os.path.join(DATA, "state")  # 抓取状态：清单 / 终态名单 / 校验基准
ASSETS = os.path.join(ROOT, "assets")                     # 资源目录
WB = os.path.join(ROOT, "_pipeline")     # 过程中文件：断点 / cookie / 临时切片（删了会丢进度）
LOGS = os.path.join(ROOT, "_logs")      # 日志与验证截图：随时可删，不影响任何数据
VID_DIR = os.path.join(ASSETS, "videos")
TMP = os.path.join(WB, "hlstmp")
CKPT = os.path.join(WB, "trailers_ckpt.json")
# “无预告片”的持久记录（与 videos_manifest.json 里“已下载/已迁移”的语义分开）：
# 确认 m3u8 返回 404 = 以后也不会有预告的番号记在这里；脚本默认直接跳过不再解析，
# 除非传 --refetch-no-preview 强制重取。放根目录便于长期保留、独立于可重置的断点。
NPV = os.path.join(STATE, "videos_no_preview.json")
COOKIE = os.path.join(WB, "javdb_cookie.txt")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"  # 必须与抓取 cf_clearance 的 Chrome UA 完全一致（CF 绑 UA+IP）
PROXY = "http://127.0.0.1:7890"

import videos_manifest as vm  # noqa: E402  复用清单判定逻辑

_ck_lock = threading.Lock()
_npv_lock = threading.Lock()


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


# ---------------- 数据加载 ----------------
def loadjs(name):
    raw = open(os.path.join(SITE, name), encoding="utf-8").read()
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])


def load_targets(variant="all"):
    """返回 [(code, vid), ...]，按榜单顺序、去重。"""
    ranks = loadjs("top250.js")["top250"]["variants"]
    movies = loadjs("movies.js")
    out, seen = [], set()
    for it in ranks.get(variant, {}).get("items", []):
        code = it["code"]
        if code in seen:
            continue
        seen.add(code)
        vid = (movies.get(code) or {}).get("id")
        out.append((code, vid))
    return out


def out_name(code):
    return code.replace("/", "_") + ".mp4"


# ---------------- 断点续传 ----------------
def load_ckpt():
    for path in (CKPT, CKPT + ".bak"):   # 主文件被写坏时回退到上一份 .bak
        if os.path.exists(path):
            try:
                return json.load(open(path, encoding="utf-8"))
            except Exception:
                continue
    return {}


def save_ckpt(d):
    with _ck_lock:
        # 先序列化并自检为合法 JSON；再把旧文件轮转为 .bak，最后写新文件并回读校验。
        # 目的：防止长任务被硬杀时正写盘导致 ckpt 被撕裂（沙箱禁 os.replace，故用“备份+回读”）。
        buf = json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True)
        json.loads(buf)  # 序列化自检：非法则抛异常、不触碰磁盘
        if os.path.exists(CKPT):
            try:
                with open(CKPT, encoding="utf-8") as f:
                    old = f.read()
                with open(CKPT + ".bak", "w", encoding="utf-8") as f:
                    f.write(old)
            except Exception:
                pass
        with open(CKPT, "w", encoding="utf-8") as f:
            f.write(buf)
            f.flush()
            os.fsync(f.fileno())


# ---------------- 无预告片持久记录 ----------------
def load_npv():
    """返回 {code: record}；文件不存在则空。"""
    if os.path.exists(NPV):
        try:
            d = json.load(open(NPV, encoding="utf-8"))
            return d.get("items", {})
        except Exception:
            return {}
    return {}


def save_npv(items):
    with _npv_lock:
        payload = {
            "version": 1,
            "desc": "确认无预告片的番号持久记录（m3u8=404，以后也不会有）；与 videos_manifest.json 的已下载/已迁移记录分开。默认跳过不重下，除非 --refetch-no-preview。",
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(items),
            "items": items,
        }
        with open(NPV, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)


def record_npv(items, code, vid, reason="m3u8_404"):
    """新增/刷新一条无预告记录（保留首次发现时间）。"""
    day = time.strftime("%Y-%m-%d")
    rec = items.get(code)
    if rec is None:
        items[code] = {"code": code, "vid": vid, "reason": reason,
                       "first_seen": day, "last_seen": day}
    else:
        rec["last_seen"] = day
        rec["reason"] = reason
        if vid:
            rec["vid"] = vid


def discard_npv(items, code):
    """当某番号后来成功下到了预告，从‘无预告’记录中移除。返回是否移除。"""
    return items.pop(code, None) is not None



# ---------------- 抓取 ----------------
def curl(url, dest=None, timeout=60, cookie=None):
    cmd = ["curl", "-sL", "--ssl-no-revoke", "--max-time", str(timeout),
           "-x", PROXY, "-A", UA]
    if cookie is not None:
        cmd += ["-H", "Cookie: " + cookie]
    cmd += ["-w", "%{http_code}"]
    if dest:
        cmd += ["-o", dest]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    code = (r.stdout or "")[-3:]
    return r, code


def get_m3u8_url(vid, cookie):
    """拿某部片的 m3u8 绝对地址；无预告片返回 None。"""
    ref = "https://javdb.com/v/%s" % vid
    txt = ""
    try:
        r = subprocess.run(
            ["curl", "-sL", "--ssl-no-revoke", "--max-time", "30", "-x", PROXY,
             "-A", UA, "-H", "Cookie: " + cookie, "-H", "Referer: " + ref,
             "https://javdb.com/v/%s/preview_url" % vid],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        txt = (r.stdout or "").strip()
    except Exception:
        return None
    if not txt:
        return None
    try:
        u = json.loads(txt).get("url", "")
    except Exception:
        return None
    if not u:
        return None
    return u if u.startswith("http") else "https://javdb.com" + u


# ---------------- 切片槽位（slot）分配：数量封顶，跑多少轮都不累积 ----------------
# 旧写法用 slot_<threading.get_ident()>，线程 id 每次重启/每新建一个线程池都不同，
# 而代码又从不删旧目录（沙盒禁批量删除），实测堆出 161 个目录 / 4.1 GB。
# 现在改成：槽号只从 0..cap-1 里抢，**主人线程已死的槽直接接管**（里面是垃圾，覆盖无害），
# 所以磁盘上最多 `cap` 份切片目录（= 并发数），不需要任何删除操作。
_slot_lock = threading.Lock()
_slot_owner = {}        # 槽号 -> 占用它的线程 id
_slot_by_thread = {}    # 线程 id -> 它占的槽号
_slot_cap = [4]         # 由主流程按 --workers 设定


def set_slot_cap(n):
    """声明本次并发数（= 槽位上限）。主流程建池前调。"""
    _slot_cap[0] = max(1, int(n or 1))


def slot_dir(tmpdir):
    """本线程用的切片目录（同一线程整轮复用同一个槽，固定文件名会被下一部覆盖）。"""
    ident = threading.get_ident()
    with _slot_lock:
        alive = {t.ident for t in threading.enumerate()}
        if ident not in alive:
            alive.add(ident)
        mine = _slot_by_thread.get(ident)
        if mine is None:
            # 顺手清理已死线程的映射，避免长循环里字典无限长
            for dead in [k for k in _slot_by_thread if k not in alive]:
                _slot_by_thread.pop(dead, None)
            for i in range(_slot_cap[0]):
                owner = _slot_owner.get(i)
                if owner is None or owner not in alive:
                    _slot_owner[i] = ident
                    _slot_by_thread[ident] = i
                    mine = i
                    break
        if mine is None:
            # 并发超出声明上限（异常用法）：退回线程 id 命名，宁可不省也不能与人撞同一目录
            return os.path.join(tmpdir, "slot_t%s" % ident)
        return os.path.join(tmpdir, "slot_%d" % mine)


def download_one(code, vid, cookie, tmpdir, retries=2):
    out_path = os.path.join(VID_DIR, out_name(code))
    if os.path.exists(out_path) and os.path.getsize(out_path) > 50000:
        return "exists", os.path.getsize(out_path)
    if not vid:
        return "novid", 0

    ref = "https://javdb.com/v/%s" % vid
    m3u8_url = get_m3u8_url(vid, cookie)
    if not m3u8_url:
        return "nopreview", 0

    # 按并发数分槽（slot_0…slot_{workers-1}，固定的 index.m3u8/key.key/seg*.ts 会被下一部覆盖）：
    # 既隔离并发，又不会像旧版按线程 id 命名那样每重启一次就多堆一摊（见 slot_dir 上方注释）
    d = slot_dir(tmpdir)
    os.makedirs(d, exist_ok=True)
    m_path = os.path.join(d, "index.m3u8")

    def fetch(url, dest, timeout=120, min_size=1, expect_code="200"):
        # 校验 HTTP 码 + 体积：CDN 偶发 403/过期签名会返回错误页(小体积/非200)，
        # 若只判非空会污染切片导致 ffmpeg 封装失败，故这里当作下载失败去重试。
        for _ in range(retries + 1):
            _, code = curl(url, dest, timeout=timeout, cookie=None)  # 段/密钥是绝对域名，无需 javdb cookie
            if (code == expect_code or expect_code is None) and os.path.exists(dest) \
                    and os.path.getsize(dest) >= min_size:
                return True
            time.sleep(1.5 + random.uniform(0, 1.5))
        return False

    # m3u8（JavDB 无论有无预告都会签出 url；真正有没有取决于此处 404）
    ok = False
    for _ in range(retries + 1):
        _, code = curl(m3u8_url, m_path, timeout=40, cookie=cookie)
        if code == "404":
            return "nopreview", 0        # 该片的预告片文件确实不存在，视为无预告（终态，重跑不再试）
        if os.path.exists(m_path) and os.path.getsize(m_path) >= 50:
            body = open(m_path, encoding="utf-8", errors="ignore").read()
            if "#EXTM3U" in body:
                ok = True
                break
        time.sleep(1.0)
    if not ok:
        return "m3u8_fail", 0

    body = open(m_path, encoding="utf-8", errors="ignore").read()
    km = re.search(r'URI="([^"]+)"', body)
    if not km:
        return "nokey", 0
    key_url = km.group(1)
    if not key_url.startswith("http"):
        key_url = "https://javdb.com" + key_url
    # 密钥固定 16 字节(AES-128)；否则视为拿到错误页，重试用
    kp = os.path.join(d, "key.key")
    if not fetch(key_url, kp, timeout=60, min_size=1) or os.path.getsize(kp) != 16:
        return "key_fail", 0

    segs = [l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")]
    if not segs:
        return "noseg", 0
    local_lines = []
    for i, s in enumerate(segs):
        surl = s if s.startswith("http") else "https://javdb.com" + s
        dest = os.path.join(d, "seg%04d.ts" % i)
        # 加密 ts 首字节不是 0x47，只能靠 HTTP200 + 合理体积(>=1KB)判断真伪
        if not fetch(surl, dest, min_size=1000):
            return "seg_fail:%d" % i, 0
        local_lines.append("seg%04d.ts" % i)

    # 本地 m3u8（保留时长信息，尽量贴近原文件）
    inf = re.findall(r'#EXTINF:[\d.]+,', body)
    out = ["#EXTM3U", '#EXT-X-KEY:METHOD=AES-128,URI="key.key"', "#EXT-X-VERSION:3",
           "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-TARGETDURATION:10"]
    for i, l in enumerate(local_lines):
        out.append(inf[i] if i < len(inf) else "#EXTINF:10.0,")
        out.append(l)
    out.append("#EXT-X-ENDLIST")
    local_m = os.path.join(d, "local.m3u8")
    open(local_m, "w", encoding="utf-8").write("\n".join(out))

    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-protocol_whitelist", "file,crypto,data", "-allowed_extensions", "ALL",
                        "-i", local_m, "-c", "copy", "-bsf:a", "aac_adtstoasc", out_path],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 10000:
        err = (r.stderr or "")[-200:]
        return "ffmpeg_fail:" + err, 0
    return "ok", os.path.getsize(out_path)


def worker(job, cookie, tmpdir, retries, item_attempts=3):
    code, vid = job
    t0 = time.time()
    # 整片级重试：key_fail/seg_fail/m3u8_fail/ffmpeg_fail 多为并发争抢代理导致的瞬断，
    # 重新签 url + 退避后重试能救回很多；nopreview/404 是终态不重试。
    st, size = "exc", 0
    for a in range(item_attempts):
        try:
            st, size = download_one(code, vid, cookie, tmpdir, retries)
        except Exception as e:
            st, size = "exc:" + str(e)[:120], 0
        base = st.split(":")[0]
        if base in ("ok", "exists", "nopreview", "novid", "nokey", "noseg"):
            break
        if a < item_attempts - 1:
            time.sleep(2 + a * 3 + random.uniform(0, 2))
    return code, st, size, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--variant", default="all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refetch-no-preview", action="store_true",
                    help="忽略 videos_no_preview.json（及断点里的 nopreview），重新尝试那些‘无预告’的番号")
    args = ap.parse_args()

    if not os.path.exists(COOKIE):
        log("缺少 cookie 文件：", COOKIE); return 2
    cookie = open(COOKIE, encoding="utf-8").read().strip()

    # 测代理（body 丢弃，只取 -w 的 http_code）
    if not os.path.exists(COOKIE):
        return
    subprocess.run  # noqa
    p = ["curl", "-sL", "--ssl-no-revoke", "--max-time", "20", "-x", PROXY,
         "-A", UA, "-H", "Cookie: " + cookie, "-o", os.devnull,
         "-w", "%{http_code}", "https://javdb.com"]
    r = subprocess.run(p, capture_output=True, text=True, encoding="utf-8", errors="replace")
    code = (r.stdout or "")[-3:]
    if code != "200":
        log("代理不通或未过 Cloudflare（javdb.com 返回 %r）。请确认 127.0.0.1:7890 在监听、cookie 有效。" % code)
        return 2

    os.makedirs(VID_DIR, exist_ok=True)
    os.makedirs(TMP, exist_ok=True)
    set_slot_cap(args.workers)      # 切片槽位数 = 并发数，磁盘占用封顶

    targets = load_targets(args.variant)
    man = vm.load()
    ckpt = load_ckpt()
    npv = load_npv()          # 无预告片持久记录 {code: record}
    npv_dirty = False

    todo = []
    skipped_manifest = skipped_done = skipped_npv = novid = 0
    for code, vid in targets:
        # 1) 已下载（含已迁移）：只认清单，即使 videos/ 为空也不重下
        if vm.match(code, man)[0] or vm.match(out_name(code), man)[0]:
            skipped_manifest += 1
            continue
        # 2) 本次任务已下成（断点 ok/exists）
        base = ckpt.get(code, {}).get("status", "").split(":")[0]
        if base in ("ok", "exists"):
            skipped_done += 1
            continue
        # 3) 无预告持久记录（或断点 nopreview）：默认直接跳过，不再解析那个网页；
        #    仅当 --refetch-no-preview 时才重试。
        if code in npv or base == "nopreview":
            if not args.refetch_no_preview:
                skipped_npv += 1
                continue
        if not vid:
            novid += 1
            continue
        todo.append((code, vid))

    log("TOP250『%s』共 %d 部；清单已下 %d；断点已下 %d；无预告(跳过) %d；无 id %d；本轮待下 %d%s"
        % (args.variant, len(targets), skipped_manifest, skipped_done, skipped_npv, novid,
           len(todo), "  [--refetch-no-preview 已开]" if args.refetch_no_preview else ""))

    if args.dry_run:
        for code, vid in todo:
            print("%s\t%s" % (code, vid))
        log("dry-run，未下载。")
        return 0

    if not todo:
        log("没有需要下载的条目，结束。")
        return 0

    done = ok = fail = 0
    vidmap = dict(todo)
    t_start = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(worker, j, cookie, TMP, args.retries): j for j in todo}
        for fu in cf.as_completed(futs):
            code, st, size, dt = fu.result()
            done += 1
            base = st.split(":")[0]
            with _ck_lock:
                ckpt[code] = {"status": st, "size": size, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            save_ckpt(ckpt)
            # 无预告持久记录：确认 404 就记下来；真下到了就从无预告记录里除掉
            vid = vidmap.get(code)
            if base == "nopreview":
                record_npv(npv, code, vid)
                npv_dirty = True
            elif base in ("ok", "exists"):
                if discard_npv(npv, code):
                    npv_dirty = True
            if base in ("ok", "exists"):
                ok += 1
            else:
                fail += 1
            log("[%d/%d] %-14s %-16s %6.1fMB %.0fs" %
                (done, len(todo), code, st, size / 1048576, dt))

    if npv_dirty:
        save_npv(npv)
        log("已更新无预告持久记录：", NPV)

    # 收尾：把新落盘的文件登记进清单（scan 幂等，只追加不删）
    log("登记 videos/ 到清单 ...")
    subprocess.run([sys.executable, os.path.join(SR, "videos_manifest.py"), "scan"],
                   capture_output=True, text=True, encoding="utf-8", errors="replace")

    log("完成：成功 %d / 失败 %d / 共 %d，用时 %.1f 分钟"
        % (ok, fail, len(todo), (time.time() - t_start) / 60))
    if fail:
        log("失败明细（非无预告、可重跑自动续传）：")
        for code, rec in ckpt.items():
            s = rec.get("status", "")
            if s.split(":")[0] not in ("ok", "exists", "nopreview"):
                log("  %-14s %s" % (code, s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
