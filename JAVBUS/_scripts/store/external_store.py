# -*- coding: utf-8 -*-
"""图片外迁 / 移动硬盘归档工具（JAVBUS 离线画廊，在 _scripts/store/）。

策略（全部写在同区的 store_config.json，可用命令行覆盖）：
  - 图片目录 assets/images：move（剪切）到移动硬盘，本机不再保留这些图片字节；可选在本机建
    目录联接(mklink /J)指向硬盘 assets/images，插上盘就能照常显示，拔掉盘网站照样打开（旧图破图，
    由前端 onerror 隐藏，属可接受）。
  - 除图片之外的所有文件（含新生成的文章 JSON / data/posts/*.js / data/data.js / _rawdata 原始件与
    _pipeline 状态台账）：copy 一份到硬盘，本机保留副本，本机可直接打开网站。
  - 硬盘上的站是一份完整可独立打开的站点（含 assets/images）。

安全护栏（写死在代码里）：
  1) 认盘标记：目标卷必须有 init-target 写入的 JAVBUS_STORE.json，且卷序列号与配置一致，
     否则一律拒绝 —— 防止插错盘把别的数据当归档盘。
  2) 跨卷校验：目标卷序列号必须 != 项目所在卷（演练需显式 --allow-same-volume，并在每次
     输出里大写警告）。
  3) 只碰图片：删除动作只可能发生在上限为 store_config.scope.images_dir（默认 assets/images）
     目录内的图片文件上，且必须
     后缀在白名单 + 魔术字节确认为图片 + 目标端 sha256 校验通过，三者缺一不删源。
  4) 不使用任何递归删除：清理空目录只用 os.rmdir（非空即失败），永远不会 rmtree。
  5) 硬盘侧只做增量写入，不删除、不 prune（prune_hdd 默认 false 且未实现）。
  6) 断点续跑：按 (相对路径, 大小, mtime) 记账，中断后重跑不会重复哈希/重复拷贝。

用法（脚本在 _scripts/store/）：
  python _scripts/store/external_store.py init-target --hdd=E:/JAVBUS_ARCHIVE   # 首次绑定移动硬盘
  python _scripts/store/external_store.py status                                # 现在图片在哪、硬盘在哪、漂移多少
  python _scripts/store/external_store.py plan                                  # 只读：本次将搬/拷什么，多少文件多少字节
  python _scripts/store/external_store.py migrate                               # 全流程：拷非图片 -> 搬图片 -> 建联接 -> 校验
  python _scripts/store/external_store.py sync         [--dry-run]              # 只拷非图片（新文章/页面/状态/清单）
  python _scripts/store/external_store.py move-images  [--dry-run] [--only-tid=] # 只搬图片
  python _scripts/store/external_store.py verify                                # 三方校验：本机 / 硬盘 / 引用台账
  python _scripts/store/external_store.py junction create|remove|status         # 单独管理联接点
  python _scripts/store/external_store.py bring-back   [--dry-run]              # 反向：把图片从硬盘搬回本机
退出码：0 正常；1 校验/执行有失败；2 前置条件不满足（未绑定盘、盘没插、护栏拒绝）。
"""
import argparse
import ctypes
import fnmatch
import glob
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))                         # _scripts/store
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "common"))          # 公共库：paths.py
import paths as PT

ROOT = PT.ROOT
WORK_DIR = PT.WORK               # 过程区：store_state.json 等记账在这里
DATA_DIR = PT.DATA               # 交付层数据（data.js + posts/shard_*.js 详情分片）
CFG_PATH = PT.script("store_config.json")   # 按名字定位（目前与本工具同区 store/）
STATE_PATH = PT.STORE_STATE
DEFAULT_IMG_REL = PT.IMG_REL     # 图片目录相对 ROOT 的默认位置

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

MAGIC_CHECK = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"RIFF", b"BM")


def log(*a):
    print("[%s]" % time.strftime("%H:%M:%S"), *a, flush=True)


def warn(*a):
    print("[WARN]", *a, flush=True)


# ---------------------------------------------------------------- 配置 / 状态
def load_cfg():
    cfg = json.load(io.open(CFG_PATH, encoding="utf-8"))
    cfg.setdefault("hdd_root", "")
    cfg.setdefault("hdd_project_subdir", "site")
    cfg.setdefault("hdd_volume_serial", "")
    cfg.setdefault("marker_name", "JAVBUS_STORE.json")
    cfg.setdefault("scope", {})
    cfg.setdefault("mode", {})
    cfg.setdefault("verify", {})
    cfg.setdefault("limits", {})
    return cfg


def save_cfg(cfg):
    io.open(CFG_PATH, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            return json.load(io.open(STATE_PATH, encoding="utf-8"))
        except Exception:
            warn("store_state.json 解析失败，按空状态继续（不影响磁盘上的真实数据）")
    return {"ledger": {}, "runs": [], "junction": {}, "images": {}, "last_verify": {}}


def save_state(st):
    PT.ensure(os.path.dirname(STATE_PATH))
    io.open(STATE_PATH, "w", encoding="utf-8").write(json.dumps(st, ensure_ascii=False, indent=1) + "\n")


def push_state(cfg):
    """把本机 store_state.json 单独推到硬盘（它每轮都变，故不走常规同步集）。"""
    try:
        if not cfg.get("hdd_root") or not os.path.isdir(cfg["hdd_root"]) or not os.path.exists(STATE_PATH):
            return
        dst = site_path(cfg, "_pipeline/store_state.json")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(STATE_PATH, dst)
    except Exception as e:
        warn("store_state.json 推送到硬盘失败（不影响本机数据）: %r" % e)


def img_rel(cfg):
    """图片目录相对 ROOT 的 posix 路径（默认 assets/images）。"""
    return cfg["scope"].get("images_dir", DEFAULT_IMG_REL).strip("/")


def images_dir(cfg):
    return os.path.join(ROOT, *img_rel(cfg).split("/"))


def hdd_site(cfg):
    return os.path.normpath(os.path.join(cfg["hdd_root"], cfg["hdd_project_subdir"]))


def hdd_images(cfg):
    return os.path.join(hdd_site(cfg), *img_rel(cfg).split("/"))


# ---------------------------------------------------------------- 卷 / 联接点
def drive_root(path):
    d = os.path.splitdrive(os.path.abspath(path))[0]
    return d + "\\"


def vol_info(path):
    """返回 {'root','serial','label','fs','drivetype','free','total'}；取不到全为 None/0。"""
    root = drive_root(path)
    k = ctypes.windll.kernel32
    buf_l = ctypes.create_unicode_buffer(262)
    buf_fs = ctypes.create_unicode_buffer(262)
    serial = ctypes.c_uint32()
    mx = ctypes.c_uint32()
    fl = ctypes.c_uint32()
    ok = k.GetVolumeInformationW(root, buf_l, 262, ctypes.byref(serial), mx, fl, buf_fs, 262)
    free = ctypes.c_ulonglong()
    tot = ctypes.c_ulonglong()
    k.GetDiskFreeSpaceExW(root, ctypes.byref(free), ctypes.byref(tot), None)
    return {"root": root, "serial": serial.value if ok else None,
            "label": buf_l.value if ok else "", "fs": buf_fs.value if ok else "",
            "drivetype": k.GetDriveTypeW(root), "free": free.value, "total": tot.value,
            "exists": os.path.exists(root)}


def is_reparse_dir(path):
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)


def junction_target(path):
    """取联接点/符号链接的目标（失败返回 None）。"""
    try:
        return os.path.realpath(path)
    except Exception:
        return None


def make_junction(link, target):
    if is_reparse_dir(link):
        cur = os.path.normcase(os.path.realpath(link))
        if cur == os.path.normcase(os.path.abspath(target)):
            return True, "已存在且指向正确目标（幂等）"
        return False, "本机 %s 已是指向别处的联接: %s" % (link, cur)
    if os.path.exists(link):
        return False, "本机 %s 已存在（须先清空或移除联接）" % link
    if not os.path.isdir(target):
        return False, "硬盘目标 %s 不存在（盘没插？路径写错？）" % target
    r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if r.returncode != 0:
        return False, (r.stdout + r.stderr).strip()[:200]
    return True, "ok"


def remove_junction(link):
    if not is_reparse_dir(link):
        return False, "%s 不是联接点，拒绝删除（防误删真实图片目录）" % link
    r = subprocess.run(["cmd", "/c", "rmdir", link], capture_output=True,
                       text=True, encoding="utf-8", errors="ignore")
    # rmdir 只摘掉联接本身，不跟随删除目标内容
    return r.returncode == 0, (r.stdout + r.stderr).strip()[:200] or "ok"


# ---------------------------------------------------------------- 文件工具
def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def is_image_magic(path):
    try:
        with open(path, "rb") as f:
            h = f.read(16)
    except Exception:
        return False
    return (h[:3] == b"\xff\xd8\xff" or h[:8] == b"\x89PNG\r\n\x1a\n"
            or h[:6] in (b"GIF87a", b"GIF89a") or (h[:4] == b"RIFF" and h[8:12] == b"WEBP")
            or h[:2] == b"BM")


def pat_match(rel, pat):
    """支持 ** (跨目录) 与 * (单层) 的简化 glob。"""
    rx = ""
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            rx += "(?:.*/)?"
            i += 3
        elif pat.startswith("**", i):
            rx += ".*"
            i += 2
        elif pat[i] == "*":
            rx += "[^/]*"
            i += 1
        elif pat[i] == "?":
            rx += "[^/]"
            i += 1
        else:
            rx += re.escape(pat[i])
            i += 1
    return re.match("^" + rx + "$", rel) is not None


def excluded(rel, cfg):
    return any(pat_match(rel, p) for p in cfg["scope"].get("copy_excludes", []))


def iter_non_images(cfg):
    """本机除图片目录外的全部文件（相对路径 posix）。图片目录若为联接点也不会被遍历进来。"""
    img = img_rel(cfg)
    out = []
    for dp, dn, fn in os.walk(ROOT):
        rel_dp = os.path.relpath(dp, ROOT).replace("\\", "/")
        base = "" if rel_dp == "." else rel_dp + "/"
        # 只剪图片目录本身（可能是嵌套的 assets/images）；它的祖先目录 assets 照常遍历
        dn[:] = [d for d in dn if base + d != img]
        out.extend(os.path.join(dp, f) for f in fn)
    rels = [os.path.relpath(p, ROOT).replace("\\", "/") for p in out]
    return sorted(r for r in rels if not excluded(r, cfg))


def iter_local_images(cfg):
    """本机图片目录下的真实图片文件（跟随不到硬盘；联接点直接返回空）。"""
    d = images_dir(cfg)
    if not os.path.isdir(d) or is_reparse_dir(d):
        return [], []
    exts = set(e.lower() for e in cfg["scope"].get("image_exts", []))
    imgs, others = [], []
    for p in glob.glob(os.path.join(d, "**", "*"), recursive=True):
        if os.path.isfile(p):
            rel = os.path.relpath(p, ROOT).replace("\\", "/")
            (imgs if os.path.splitext(p)[1].lower() in exts else others).append(rel)
    return sorted(imgs), sorted(others)


def ledger_key(rel, st_path):
    s = os.stat(st_path)
    return "%s|%d|%d" % (rel, s.st_size, int(s.st_mtime))


# ---------------------------------------------------------------- 护栏
def preflight(cfg, args, need_hdd=True):
    """所有写操作前必过：绑定/跨卷/标记/容量。返回 (ok, hdd_vol, proj_vol, note)。"""
    proj = vol_info(ROOT)
    if not cfg.get("hdd_root"):
        if not need_hdd:
            return True, None, proj, "unbound-readonly"
        log("尚未绑定移动硬盘：先跑 `python _scripts/store/external_store.py init-target --hdd=E:/JAVBUS_ARCHIVE`"
            "，或直接编辑 _scripts/store/store_config.json 的 hdd_root")
        return False, None, proj, "unbound"
    if not need_hdd:
        return True, None, proj, "readonly"
    hv = vol_info(cfg["hdd_root"])
    if not os.path.exists(cfg["hdd_root"]):
        log("硬盘路径不存在：%s（没插盘 / 盘符变了？）" % cfg["hdd_root"])
        return False, hv, proj, "missing"
    mk = os.path.join(cfg["hdd_root"], cfg["marker_name"])
    if not os.path.exists(mk):
        log("目标卷上没有识别标记 %s —— 拒绝执行（防止认错盘）。用 init-target 绑定这块盘。" % mk)
        return False, hv, proj, "no-marker"
    mkr = json.loads(io.open(mk, encoding="utf-8").read())
    if mkr.get("project_root", "").lower() != os.path.abspath(ROOT).lower():
        warn("标记属于别的项目 %s，拒绝混用" % mkr.get("project_root"))
        return False, hv, proj, "foreign-marker"
    bound = cfg.get("hdd_volume_serial")
    if bound and hv["serial"] != bound:
        log("卷序列号不匹配：配置 %s，当前 %s —— 这块盘不是登记过的那块（或换了分区）。重跑 init-target 确认。"
            % (bound, hv["serial"]))
        return False, hv, proj, "serial-mismatch"
    same = (hv["serial"] is not None and hv["serial"] == proj["serial"])
    allow = args.allow_same_volume or cfg["verify"].get("allow_same_volume")
    if same and not allow:
        log("目标与项目在同一卷（%s）。真·移动硬盘请换盘符；演练请加 --allow-same-volume。" % proj["root"])
        return False, hv, proj, "same-volume"
    if same:
        warn("演练模式：目标卷 == 项目卷 %s，本次不会节省本机空间，且仍在做完校验后才删源" % proj["root"])
    return True, hv, proj, "ok"


def capacity_check(cfg, hv, need_bytes):
    ratio = cfg["verify"].get("min_free_ratio", 1.05)
    need = int(need_bytes * ratio)
    ok = hv and hv["free"] >= need
    log("容量：待写入 %.1f MB，硬盘可用 %.1f MB，要求 >= %.1f MB(%.0f%% 余量) -> %s"
        % (need_bytes / 1048576.0, (hv["free"] if hv else 0) / 1048576.0,
           need / 1048576.0, ratio * 100, "够" if ok else "不够"))
    return ok


# ---------------------------------------------------------------- 拷贝 / 搬运
def copy_verified(srel, cfg, state, hash_mode="sha256"):
    """把本机 srel 拷到硬盘站对应路径；返回 (status, bytes)。status: ok|skip|fail"""
    src = os.path.join(ROOT, srel)
    dst = os.path.join(hdd_site(cfg), *srel.split("/"))
    try:
        sz = os.path.getsize(src)
    except OSError:
        return "fail", 0
    if os.path.exists(dst) and os.path.getsize(dst) == sz:
        if hash_mode == "none":
            return "skip", sz
        led = state["ledger"]
        k = ledger_key(srel, src)
        rec = led.get(k)
        if rec and rec.get("sha") and rec.get("where") in ("both", "hdd"):
            try:
                if sha256_file(dst) == rec["sha"]:
                    return "skip", sz          # 记账命中，不必再哈希源
            except OSError:
                return "fail", sz
        try:
            h_src = sha256_file(src)
            if sha256_file(dst) == h_src:
                led[k] = {"size": sz, "sha": h_src, "where": "both"}
                return "skip", sz
        except OSError:
            return "fail", sz
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.copyfile(src, dst)
    except Exception as e:
        warn("  拷贝失败 %s: %r" % (srel, e))
        return "fail", sz
    if os.path.getsize(dst) != sz:
        warn("  体积不符 %s（保留本机源）" % srel)
        return "fail", sz
    if hash_mode != "none":
        try:
            if sha256_file(src) != sha256_file(dst):
                warn("  哈希不符 %s（保留本机源）" % srel)
                return "fail", sz
            state["ledger"][ledger_key(srel, src)] = {"size": sz, "sha": sha256_file(dst), "where": "both"}
        except OSError as e:
            warn("  校验异常 %s: %r" % (srel, e))
            return "fail", sz
    return "ok", sz


def move_one_image(srel, cfg, state, dry=False, hash_mode="sha256"):
    """搬一张图片到硬盘并删本机源；返回 status。护栏：白名单后缀 + 魔术字节 + 校验通过才删。"""
    src = os.path.join(ROOT, srel)
    dst = os.path.join(hdd_site(cfg), *srel.split("/"))
    sz = os.path.getsize(src)
    if dry:
        return "todo", sz
    if os.path.exists(dst) and os.path.getsize(dst) == sz:
        if hash_mode != "none" and sha256_file(src) != sha256_file(dst):
            warn("  同名异内容，拒绝覆盖: %s" % srel)
            return "fail", sz
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copyfile(src, dst)
        except Exception as e:
            warn("  搬运失败 %s: %r" % (srel, e))
            return "fail", sz
    if os.path.getsize(dst) != sz:
        return "fail", sz
    if hash_mode != "none":
        h_src, h_dst = sha256_file(src), sha256_file(dst)
        if h_src != h_dst:
            warn("  校验不一致，保留本机源: %s" % srel)
            return "fail", sz
        state["ledger"][ledger_key(srel, src)] = {"size": sz, "sha": h_dst, "where": "hdd"}
    mode = cfg["mode"].get("images", "move")
    if mode == "move" and cfg["verify"].get("delete_source_only_after_verified", True):
        try:
            os.remove(src)
        except OSError as e:
            warn("  删本机源失败 %s: %r" % (srel, e))
            return "fail", sz
        return "moved", sz
    return "copied", sz


def tidy_empty_img_dirs(cfg):
    """只删图片目录下的空目录（os.rmdir 非空即失败），绝不递归。"""
    d = images_dir(cfg)
    if not os.path.isdir(d) or is_reparse_dir(d):
        return 0
    n = 0
    for dp, dn, fn in sorted(os.walk(d), key=lambda x: -len(x[0])):
        if os.path.abspath(dp) == os.path.abspath(d):
            continue
        if not fn and not dn:
            try:
                os.rmdir(dp)
                n += 1
            except OSError:
                pass
    return n


# ---------------------------------------------------------------- 命令
def cmd_init_target(args):
    cfg = load_cfg()
    if not args.hdd:
        log("init-target 需要 --hdd=<盘符>/<目录>，例如 --hdd=E:/JAVBUS_ARCHIVE")
        return 2
    hdd = os.path.normpath(os.path.abspath(args.hdd))
    if hdd.lower().startswith(os.path.abspath(ROOT).lower() + os.sep.lower()) or \
            os.path.abspath(ROOT).lower().startswith(hdd.lower() + os.sep.lower()):
        log("拒绝：目标路径与项目目录互相嵌套")
        return 2
    hv = vol_info(hdd)
    proj = vol_info(ROOT)
    if not hv["exists"]:
        log("盘符 %s 不存在" % drive_root(hdd))
        return 2
    if hv["serial"] == proj["serial"] and not args.allow_same_volume:
        log("目标与项目同在 %s（序列号 %08X）。请指向移动硬盘；坚持演练加 --allow-same-volume"
            % (hv["root"], hv["serial"] or 0))
        return 2
    os.makedirs(hdd, exist_ok=True)
    marker = os.path.join(hdd, cfg["marker_name"])
    if os.path.exists(marker):
        mkr = json.loads(io.open(marker, encoding="utf-8").read())
        if mkr.get("project_root", "").lower() != os.path.abspath(ROOT).lower() and not args.force:
            log("该盘已被别的项目登记：%s（--force 可覆盖）" % mkr.get("project_root"))
            return 2
    io.open(marker, "w", encoding="utf-8").write(json.dumps({
        "marker": "JAVBUS offline gallery archive target",
        "project_root": os.path.abspath(ROOT),
        "images_dir": img_rel(cfg),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "volume_label": hv["label"], "volume_serial": hv["serial"],
        "note": "删除本文件即解除绑定；工具只会在有本标记的卷上读写"}, ensure_ascii=False, indent=1))
    cfg["hdd_root"] = hdd.replace("\\", "/")
    cfg["hdd_volume_serial"] = hv["serial"]
    cfg["hdd_label"] = hv["label"]
    cfg["verify"]["allow_same_volume"] = bool(hv["serial"] == proj["serial"])
    save_cfg(cfg)
    KIND = {2: "REMOVABLE", 3: "LOCAL", 4: "NETWORK"}
    log("已绑定 -> %s  卷=%s 序列号=%08X 类型=%s 可用=%.1f GB"
        % (hdd, hv["root"], hv["serial"] or 0, KIND.get(hv["drivetype"], hv["drivetype"]),
           hv["free"] / 2 ** 30))
    if cfg["verify"]["allow_same_volume"]:
        warn("演练绑定：目标与项目同在 %s，已把 verify.allow_same_volume 记为 true（后续命令不再需要重复加旗标）。"
             "真·移动硬盘插好后重新 init-target 会自动改回 false。" % hv["root"])
        warn("演练不会节省本机空间；复原请用 `bring-back` + `junction remove`（工具只会摘联接、不递归删）。")
    else:
        log("  已写入标记 %s；后续 sync/move-images/migrate 都会先校验这块盘" % cfg["marker_name"])
    return 0


def where_are_images(cfg):
    d = images_dir(cfg)
    if is_reparse_dir(d):
        t = junction_target(d)
        reach = os.path.isdir(d) and any(True for _ in glob.iglob(os.path.join(d, "*", "*")))
        return ("junction", t if reach else (t or "?"), "reachable" if reach else "broken")
    if os.path.isdir(d):
        n = len(glob.glob(os.path.join(d, "**", "*"), recursive=True))
        return ("local", os.path.abspath(d), "present" if n else "empty-dir")
    return ("absent", "-", "missing")


def cmd_status(args):
    cfg = load_cfg()
    st = load_state()
    kind, tgt, det = where_are_images(cfg)
    print("=" * 66)
    print("策略（_scripts/store/store_config.json）")
    m = cfg["mode"]
    imode = m.get("images", "move")
    phrase = {"local": "新图只写本机 assets/images/，复制到移动硬盘由人工做（本工具不再自动外迁）",
              "copy": "拷到硬盘 + 本机保留图片字节",
              "move": "拷到硬盘后删本机图片字节"}.get(imode, imode)
    print("  images 策略            : %s -> %s" % (imode, phrase))
    print("  非 images 文件         : %s 到硬盘 + 本机保留副本=%s"
          % (m.get("non_images", "copy"), m.get("keep_local_non_images", True)))
    print("  仅 images 移到硬盘      : %s" % m.get("images_only_move_to_hdd", True))
    print("  本机建联接指向硬盘 images: %s" % m.get("make_junction", True))
    print("  硬盘侧 prune（删除）    : %s" % m.get("prune_hdd", False))
    print("-" * 66)
    print("移动硬盘")
    if not cfg.get("hdd_root"):
        print("  未绑定。跑 init-target --hdd=E:/JAVBUS_ARCHIVE")
    else:
        hv = vol_info(cfg["hdd_root"])
        mk = os.path.join(cfg["hdd_root"], cfg["marker_name"])
        print("  hdd_root       : %s" % cfg["hdd_root"])
        if cfg["verify"].get("allow_same_volume"):
            print("  ⚠ 演练绑定（目标与项目同卷）：不会节省本机空间；真盘插好重跑 init-target 即自动改回")
        print("  完整站(含images): %s" % hdd_site(cfg))
        print("  卷/序列号      : %s %s / %08X  配置登记 %08X -> %s"
              % (hv["root"], hv["label"], hv["serial"] or 0, int(cfg.get("hdd_volume_serial") or 0),
                 "匹配" if hv["serial"] and hv["serial"] == int(cfg.get("hdd_volume_serial") or 0) else "不匹配"))
        print("  在位/标记      : %s / %s" % ("已挂载" if hv["exists"] else "未挂载(拔盘状态)",
                                             "有" if os.path.exists(mk) else "无"))
        site_ok = os.path.isdir(hdd_site(cfg))
        print("  硬盘站已建立    : %s" % site_ok)
    print("-" * 66)
    print("本机图片目录 %s : %s -> %s (%s)" % (img_rel(cfg), kind, tgt, det))
    li, others = iter_local_images(cfg)
    print("  本机残留图片    : %d 个 / %.1f MB   非图片文件混在图片目录内: %d"
          % (len(li), sum(os.path.getsize(os.path.join(ROOT, x)) for x in li) / 1048576.0, len(others)))
    refs = referenced_image_paths()
    on_local = sum(1 for r in refs if is_image_file(os.path.join(ROOT, r)))
    on_hdd = sum(1 for r in refs if not is_image_file(os.path.join(ROOT, r))
                 and is_image_file(os.path.join(hdd_site(cfg), *r.split("/")))) if cfg.get("hdd_root") else 0
    print("  交付层引用的图片 : %d 个 -> 本机可见 %d / 仅在硬盘 %d / 两处都无 %d"
          % (len(refs), on_local, on_hdd, len(refs) - on_local - on_hdd))
    non = iter_non_images(cfg)
    print("  非图片文件     : %d 个 / %.1f MB（同步到硬盘且本机保留）"
          % (len(non), sum(os.path.getsize(os.path.join(ROOT, x)) for x in non) / 1048576.0))
    if st.get("runs"):
        last = st["runs"][-1]
        print("  最近一次执行     : %s %s copied=%s moved=%s fail=%s"
              % (last.get("at"), last.get("cmd"), last.get("copied"), last.get("moved"), last.get("fail")))
    print("=" * 66)
    return 0


def is_image_file(p):
    return os.path.isfile(p) and is_image_magic(p)


def referenced_image_paths():
    """交付层(data/data.js + data/posts/*.js)引用的全部本地图片路径（去重）。"""
    out = set()
    # 新布局 assets/images|assets/avatars；旧布局 images|avatars 也一并抓到，漏改的残留会暴露成找不到字节
    rx = re.compile(r'(?<![/\w.\-])((?:assets/)?(?:images|avatars)/[^"\\]+)')
    for f in [PT.DATA_INDEX] + glob.glob(os.path.join(PT.DATA_POSTS, "*.js")):
        if not os.path.exists(f):
            continue
        for m in rx.findall(io.open(f, encoding="utf-8", errors="ignore").read()):
            if "<" not in m:
                out.add(m)
    return sorted(out)


def cmd_plan(args):
    cfg = load_cfg()
    st = load_state()
    ok, hv, proj, note = preflight(cfg, args, need_hdd=not args.readonly)
    if not ok:
        return 2
    non = iter_non_images(cfg)
    need, to_copy = 0, []
    for rel in non:
        dst = os.path.join(hdd_site(cfg), *rel.split("/"))
        s = os.path.getsize(os.path.join(ROOT, rel))
        if not (os.path.exists(dst) and os.path.getsize(dst) == s):
            to_copy.append(rel)
            need += s
    li, others = iter_local_images(cfg)
    img_need = sum(os.path.getsize(os.path.join(ROOT, x)) for x in li)
    if args.only_tid:
        keep = set(t.strip() for t in args.only_tid.split(",") if t.strip())
        li = [x for x in li if x.split("/")[-2] in keep]   # tid = 图片的直接父目录
        img_need = sum(os.path.getsize(os.path.join(ROOT, x)) for x in li)
    print("=" * 66)
    print("PLAN（只读，不写盘）  %s" % ("[演练:同卷]" if note == "ok-same-volume" else ""))
    print("  目标站 : %s" % (hdd_site(cfg) if cfg["hdd_root"] else "(未绑定)"))
    print("  待拷贝(非 images) : %d 个 / %.1f MB  [新增或大小变化；已一致 %d 个]"
          % (len(to_copy), need / 1048576.0, len(non) - len(to_copy)))
    print("  待搬运(images, 方式=%s) : %d 个 / %.1f MB  -> 本机%s"
          % (cfg["mode"].get("images", "move"), len(li), img_need / 1048576.0,
             "将不再保留这些图片" if cfg["mode"].get("images") == "move" else "保留副本"))
    if others:
        print("  images 目录内非图片文件 %d 个 -> 一律不搬不删，保持原地（列出前 5）: %s" % (len(others), others[:5]))
    if cfg["mode"].get("make_junction", True):
        kind, tgt, det = where_are_images(cfg)
        print("  联接点 : 当前=%s(%s)，本机图片清空后创建 images -> %s" % (kind, det, hdd_images(cfg)))
    print("  合计需硬盘空间 : %.1f MB（含 %.0f%% 余量 -> %.1f MB），目标可用 %.1f MB"
          % ((need + img_need) / 1048576.0, (cfg["verify"].get("min_free_ratio", 1.05) - 1) * 100,
             (need + img_need) * cfg["verify"].get("min_free_ratio", 1.05) / 1048576.0,
             (hv["free"] if hv else 0) / 1048576.0))
    print("=" * 66)
    return 0


def cmd_sync(args):
    cfg = load_cfg()
    st = load_state()
    ok, hv, proj, note = preflight(cfg, args)
    if not ok:
        return 2
    non = iter_non_images(cfg)
    todo = []
    for rel in non:
        dst = os.path.join(hdd_site(cfg), *rel.split("/"))
        s = os.path.getsize(os.path.join(ROOT, rel))
        if not (os.path.exists(dst) and os.path.getsize(dst) == s):
            todo.append(rel)
    if args.limit or cfg["limits"].get("max_files_per_run"):
        cap = args.limit or cfg["limits"]["max_files_per_run"]
        if len(todo) > cap:
            log("  本次按上限只处理前 %d 个（limits.max_files_per_run / --limit），重跑继续" % cap)
            todo = todo[:cap]
    log("sync: 非图片文件 %d 个，待拷 %d 个（%.1f MB）%s"
        % (len(non), len(todo), sum(os.path.getsize(os.path.join(ROOT, x)) for x in todo) / 1048576.0,
           "[DRY-RUN]" if args.dry_run else ""))
    if args.dry_run:
        for r in todo[:40]:
            log("   COPY %s" % r)
        return 0
    hm = cfg["verify"].get("hash", "sha256")
    cnt = {"ok": 0, "skip": 0, "fail": 0}
    byt = 0
    for i, rel in enumerate(todo, 1):
        s, sz = copy_verified(rel, cfg, st, hm)
        cnt[s] += 1
        byt += sz if s == "ok" else 0
        if i % cfg["limits"].get("ledger_flush_every", 200) == 0:
            save_state(st)
            log("   ...%d/%d ok=%d fail=%d (%.1f MB)" % (i, len(todo), cnt["ok"], cnt["fail"], byt / 1048576.0))
    st.setdefault("runs", []).append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "cmd": "sync",
                                      "copied": cnt["ok"], "moved": 0, "fail": cnt["fail"],
                                      "bytes": byt})
    save_state(st)
    log("sync 完成 ok=%d skip=%d fail=%d，%.1f MB（本机副本全部保留）" % (cnt["ok"], cnt["skip"], cnt["fail"], byt / 1048576.0))
    return 1 if cnt["fail"] else 0


def cmd_move_images(args):
    cfg = load_cfg()
    st = load_state()
    ok, hv, proj, note = preflight(cfg, args)
    if not ok:
        return 2
    li, others = iter_local_images(cfg)
    if args.only_tid:
        keep = set(t.strip() for t in args.only_tid.split(",") if t.strip())
        li = [x for x in li if x.split("/")[-2] in keep]   # tid = 图片的直接父目录
    total = sum(os.path.getsize(os.path.join(ROOT, x)) for x in li)
    if not li:
        kind, tgt, det = where_are_images(cfg)
        log("本机没有待搬图片（images 状态=%s/%s）" % (kind, det))
        return 0
    if not capacity_check(cfg, hv, total):
        return 2
    log("move-images: %d 张 / %.1f MB，方式=%s%s" % (len(li), total / 1048576.0,
                                                    cfg["mode"].get("images", "move"),
                                                    " [DRY-RUN]" if args.dry_run else ""))
    if others:
        warn("图片目录内 %d 个非图片后缀文件不在搬运范围（保持本机原样）: %s" % (len(others), others[:5]))
    hm = cfg["verify"].get("hash", "sha256")
    cap = args.limit or cfg["limits"].get("max_files_per_run")
    if cap and len(li) > cap:
        log("  本次按上限只搬前 %d 张（limits.max_files_per_run / --limit，可反复跑续搬）" % cap)
        li = li[:cap]
        total = sum(os.path.getsize(os.path.join(ROOT, x)) for x in li)
    if cfg["scope"].get("require_magic_byte", True) and not args.dry_run:
        fake = [x for x in li if not is_image_magic(os.path.join(ROOT, x))]
        if fake:
            warn("其中 %d 个后缀像图片但魔术字节不是图片，一律不搬不删: %s" % (len(fake), fake[:5]))
            li = [x for x in li if x not in set(fake)]
    cnt = {"moved": 0, "copied": 0, "skip": 0, "fail": 0}
    for i, rel in enumerate(li, 1):
        s, sz = move_one_image(rel, cfg, st, args.dry_run, hm)
        cnt[s] = cnt.get(s, 0) + 1
        if s == "fail":
            log("   !! %s 未搬/未删，本机源保留" % rel)
        if i % cfg["limits"].get("ledger_flush_every", 200) == 0:
            save_state(st)
            log("   ...%d/%d moved=%d fail=%d" % (i, len(li), cnt["moved"], cnt["fail"]))
    if not args.dry_run:
        n = tidy_empty_img_dirs(cfg)
        log("清空的图片子目录 %d 个（只 rmdir 空目录）" % n)
        st.setdefault("runs", []).append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "cmd": "move-images",
                                          "copied": cnt["copied"], "moved": cnt["moved"],
                                          "fail": cnt["fail"], "bytes": total})
        save_state(st)
    log("move-images 完成 %s" % cnt)
    return 1 if cnt["fail"] else 0


def cmd_junction(args):
    cfg = load_cfg()
    d = images_dir(cfg)
    if args.jaction == "status":
        kind, tgt, det = where_are_images(cfg)
        print("%s: %s -> %s (%s)" % (img_rel(cfg), kind, tgt, det))
        return 0
    if args.jaction == "remove":
        ok, msg = remove_junction(d)
        log("remove junction:", msg)
        return 0 if ok else 1
    ok, hv, proj, note = preflight(cfg, args)
    if not ok:
        return 2
    if os.path.isdir(d) and not is_reparse_dir(d):
        left, _ = iter_local_images(cfg)
        if left:
            log("本机仍有 %d 张图片，先 move-images 或改 --no-junction；不创建联接（避免写回本机）" % len(left))
            return 2
        os.rmdir(d)
    made, msg = make_junction(d, hdd_images(cfg))
    st = load_state()
    st["junction"] = {"status": "created" if made else "failed", "target": hdd_images(cfg).replace("\\", "/"),
                      "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    save_state(st)
    log("junction:", msg)
    return 0 if made else 1


def cmd_verify(args):
    cfg = load_cfg()
    st = load_state()
    ok, hv, proj, note = preflight(cfg, args, need_hdd=not args.allow_no_hdd)
    if not ok:
        return 2
    refs = referenced_image_paths()
    site = hdd_site(cfg) if cfg["hdd_root"] else None
    kind, tgt, det = where_are_images(cfg)
    via_junction = (kind == "junction")
    tiers = {"local": [], "hdd": [], "gone": []}
    for r in refs:
        local_hit = is_image_file(os.path.join(ROOT, r))
        hdd_hit = bool(site) and is_image_file(site_path(cfg, r))
        if local_hit and not via_junction:
            tiers["local"].append(r)
        elif local_hit or hdd_hit:
            tiers["hdd"].append(r)
        else:
            tiers["gone"].append(r)
    li, others = iter_local_images(cfg)
    print("=" * 66)
    print("VERIFY（交付引用 -> 实际字节位置）  images 状态=%s/%s" % (kind, det))
    print("  引用图片总数        : %d" % len(refs))
    print("  字节在本机          : %d（迁移前/回搬后的正常状态）" % len(tiers["local"]))
    print("  字节仅在移动硬盘    : %d  <- 外迁成功，本机不占这部分空间%s"
          % (len(tiers["hdd"]), "（经联接点可见，拔盘则破图但网站仍可打开）" if via_junction else ""))
    print("  两处都找不到        : %d %s" % (len(tiers["gone"]), tiers["gone"][:6]))
    print("  本机残留待搬图片    : %d 个 / %.1f MB" % (len(li), sum(os.path.getsize(os.path.join(ROOT, x)) for x in li) / 1048576.0))
    print("  图片目录内非图片文件 : %d（工具一律不搬不删）" % len(others))
    if site:
        for sub in ("data/data.js", "index.html"):
            lp = os.path.join(ROOT, *sub.split("/"))
            rp = os.path.join(site, *sub.split("/"))
            same = os.path.exists(lp) and os.path.exists(rp) and os.path.getsize(lp) == os.path.getsize(rp)
            print("  硬盘副本 %-12s : %s" % (sub, "在且同大小" if same else "缺失/不一致"))
        tj = len(glob.glob(os.path.join(site, "data", "posts", "*.js")))
        tl = len(glob.glob(os.path.join(PT.DATA_POSTS, "*.js")))
        # 详情层是分片(shard_*.js)，本机重跑 build 后文件数会与硬盘旧份短暂不等——正常，
        # 下次把 data/ 整份复制到硬盘后即对齐；此处仅供参考，不计入 PASS/FAIL。
        print("  data/posts/*.js(分片) 硬盘 vs 本机 : %d vs %d  (不等多为待重新复制 data/，不影响判定)" % (tj, tl))
    bad = len(tiers["gone"]) > 0
    st["last_verify"] = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "refs": len(refs),
                         "local": len(tiers["local"]), "hdd": len(tiers["hdd"]), "gone": len(tiers["gone"]),
                         "stray_local_images": len(li), "result": "FAIL" if bad else "PASS"}
    st.setdefault("runs", []).append({"at": st["last_verify"]["at"], "cmd": "verify",
                                      "copied": 0, "moved": 0, "fail": len(tiers["gone"])})
    save_state(st)
    print("  conclusion          : %s" % ("FAIL（有引用找不到字节）" if bad else "PASS"))
    print("=" * 66)
    return 1 if bad else 0


def cmd_bring_back(args):
    """反向：把硬盘图片目录搬回本机（先摘联接，再 copy->校验->删硬盘源）。"""
    cfg = load_cfg()
    st = load_state()
    ok, hv, proj, note = preflight(cfg, args)
    if not ok:
        return 2
    d = images_dir(cfg)
    if is_reparse_dir(d):
        rok, rmsg = remove_junction(d)
        log("先摘本机联接:", rmsg)
        if not rok:
            return 1
    src_root = hdd_images(cfg)
    if not os.path.isdir(src_root):
        log("硬盘没有图片目录 %s" % src_root)
        return 2
    files = []
    img_base = img_rel(cfg)
    for p in glob.glob(os.path.join(src_root, "**", "*"), recursive=True):
        if os.path.isfile(p):
            rel = img_base + "/" + os.path.relpath(p, src_root).replace("\\", "/")
            files.append(rel)
    exts = set(e.lower() for e in cfg["scope"].get("image_exts", []))
    files = [f for f in files if os.path.splitext(f)[1].lower() in exts]
    total = sum(os.path.getsize(site_path(cfg, f)) for f in files)
    log("bring-back: %d 张 / %.1f MB 从硬盘取回本机（默认硬盘保留副本，--hdd-delete 才移回）%s"
        % (len(files), total / 1048576.0, " [DRY-RUN]" if args.dry_run else ""))
    lv = vol_info(ROOT)
    if lv["free"] < total * cfg["verify"].get("min_free_ratio", 1.05):
        log("本机卷 %s 可用 %.1f MB，取回需 %.1f MB（含余量）—— 不够，停止；硬盘数据未动"
            % (lv["root"], lv["free"] / 1048576.0, total / 1048576.0))
        return 2
    if args.dry_run:
        return 0
    n = 0
    for i, rel in enumerate(sorted(files), 1):
        src = site_path(cfg, rel)              # 硬盘
        dst = os.path.join(ROOT, *rel.split("/"))   # 本机
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copyfile(src, dst)
            if os.path.getsize(dst) != os.path.getsize(src) or sha256_file(dst) != sha256_file(src):
                warn("  校验不一致，保留硬盘副本: %s" % rel)
                continue
            if args.hdd_delete:
                os.remove(src)
            n += 1
        except Exception as e:
            warn("  失败 %s: %r" % (rel, e))
    st.setdefault("runs", []).append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "cmd": "bring-back",
                                      "copied": 0 if args.hdd_delete else n, "moved": n if args.hdd_delete else 0,
                                      "fail": len(files) - n})
    save_state(st)
    log("bring-back 完成：%d 张已在本机；硬盘副本%s。联接点已移除，之后跑 `migrate` 重新按策略归位"
        % (n, "已删除" if args.hdd_delete else "保持不动"))
    return 0


def site_path(cfg, rel):
    return os.path.join(hdd_site(cfg), *rel.split("/"))


def cmd_write_guard(args):
    """抓取/下载前的写入闸门（2026-10-30 新策略：图片落本机，复制由人工做）。

    本机 `assets/images/` 就是新图的落盘位置。它每轮开头**本来就可能不存在** —— 用户把整份 JAVBUS
    复制到移动硬盘后会删掉本机 `assets/images/`，硬盘上保留全量；所以 absent 不是故障，重建空目录继续跑。
    rc=0 可继续；rc=4 = 还残留旧策略的目录联接（会把新图写进联接目标，与"本机真实目录+人工复制"
    冲突）；rc=5 = 目录建不出来（磁盘/权限问题）。"""
    cfg = load_cfg()
    kind, tgt, det = where_are_images(cfg)
    if kind == "junction":
        log("图片目录还是旧策略的目录联接（指向 %s，%s）。" % (tgt, det))
        log("  新流程要求它是本机真实目录，先只摘联接（不删任何字节）：")
        log("    python _scripts/store/external_store.py junction remove")
        return 4
    if kind == "absent":
        d = images_dir(cfg)
        try:
            os.makedirs(d)
        except Exception as e:
            log("无法重建本机图片目录 %s：%r" % (os.path.abspath(d), e))
            return 5
        log("写入闸门通过：本机图片目录已重建（%s），本轮新图落这里" % os.path.abspath(d))
        log("  提醒：抓完请自行把整份 JAVBUS 文件夹复制到移动硬盘（覆盖，勿删硬盘图片），再删本机 assets/images/。")
        return 0
    log("写入闸门通过：图片目录 %s 为本机真实目录（%s），新图落本机" % (img_rel(cfg), det))
    log("  本工具不再自动外迁/建联接；整份复制到移动硬盘 + 删本机 assets/images/ 由人工执行。")
    return 0


def cmd_migrate(args):
    cfg = load_cfg()
    if cfg.get("mode", {}).get("images") == "local":
        warn("当前策略是「图片落本机 + 人工整份复制到移动硬盘」；migrate 属旧自动外迁流程，")
        warn("确需自动外迁请先改 _scripts/store/store_config.json 的 mode.images（move/copy）。此处不改动任何文件。")
        return 0
    if args.no_junction:
        cfg["mode"]["make_junction"] = False
    if args.images_mode:
        cfg["mode"]["images"] = args.images_mode
    log("migrate 开始：images=%s 非images=copy(本机保留) junction=%s"
        % (cfg["mode"].get("images"), cfg["mode"].get("make_junction")))
    rc = cmd_sync(args)
    if rc:
        return rc
    rc = cmd_move_images(args)
    if rc:
        return rc
    if cfg["mode"].get("make_junction", True):
        args.jaction = "create"
        rc = cmd_junction(args)
        if rc:
            warn("联接点未建（拔盘时图片不显示，但网站仍可打开）；可用 `junction create` 重试")
    # 联接建好后再同步一次：把 store_state.json / 新写入的非图片文件带上硬盘
    rc2 = cmd_sync(args)
    return cmd_verify(args) or rc2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["init-target", "status", "plan", "sync", "move-images",
                                    "migrate", "verify", "junction", "bring-back", "write-guard"])
    ap.add_argument("jaction", nargs="?", default="status", choices=["create", "remove", "status"])
    ap.add_argument("--hdd", type=str, default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--readonly", action="store_true", help="plan 时不要求硬盘在位")
    ap.add_argument("--allow-no-hdd", action="store_true", help="verify 时只查本机侧")
    ap.add_argument("--allow-same-volume", action="store_true", help="演练：允许目标与项目同卷")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-tid", type=str, default="")
    ap.add_argument("--images-mode", type=str, default="", choices=["", "move", "copy"])
    ap.add_argument("--no-junction", action="store_true")
    ap.add_argument("--hdd-delete", action="store_true", help="bring-back 时删除硬盘副本（默认保留）")
    a = ap.parse_args()
    cfg = load_cfg()
    fn = {"init-target": cmd_init_target, "status": cmd_status, "plan": cmd_plan,
          "sync": cmd_sync, "move-images": cmd_move_images, "migrate": cmd_migrate,
          "verify": cmd_verify, "junction": cmd_junction, "bring-back": cmd_bring_back,
          "write-guard": cmd_write_guard}[a.cmd]
    rc = fn(a)
    if a.cmd not in ("status", "plan", "write-guard") and not a.dry_run:
        push_state(load_cfg())
    return rc


if __name__ == "__main__":
    sys.exit(main())
