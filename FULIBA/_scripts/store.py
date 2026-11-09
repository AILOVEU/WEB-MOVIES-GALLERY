# -*- coding: utf-8 -*-
"""
store.py — 图片库位置层（单一事实源）

背景：图片库有 14 万+ 文件 / 30+ GB，整体搬运由使用者自行完成（本项目**不提供**
搬运脚本，也不关心图片被搬到哪块盘）。站点里的图片 href 是 `assets/images/...`
（2026-11-04 目录重构：图片库从根 `images/` 并入 `assets/`），而台账与容器里的
**图片键**一律仍是 `images/<xx>/<hash>.ext`，两套前缀由 common.site_rel() 翻译，
所以换图片库位置不用重写 parsed / data.js / 单篇页 / manifest。
图床可达时网站图全在，不可达时网站照样打开、只是旧文章图片显示不出来。
填好 `_pipeline/images_store.json` 的 `external_root` 后本模块会自动在
`assets/images` 建 junction。

本模块只管两件事：**库在哪**（状态判定 + junction 自愈）和**跑增量时能不能动图片层**（闸门）。

五种状态：
  local        mode=local，assets/images 是本机真实目录且**包含全部历史图**（搬运前的默认形态）
  attached     mode=external，junction 可达且图片库在（读写都直通外置库）
  detached     mode=external，库不可达（图片步骤必须跳过，绝不能下载/清理）
  incremental  mode=incremental，**历史图已被移到别的电脑（本机拿不到）**，
               本机 `assets/images/` 是真实目录、只用来接**增量新图**：
               manifest 里有条目 = 历史已下载（不重下、不清台账），只下 manifest 里没有的 URL
  broken       mode=external，但本机 assets/images 是个有内容的真实目录
               = 有人在库不可达状态下跑过增量，必须人工处置

用法：
  _scripts\\py.cmd _scripts\\store.py --status        人读状态（含容量/挂载点/条目数）
  _scripts\\py.cmd _scripts\\store.py --json          机读状态
  脚本里：import store; store.guard_or_exit("s04i")   # 非可写态直接退出
          store.new_images_only()          # True = 历史图不在本机，只处理增量新图

约定：本模块**不得** import common（common 要 import 本模块）。
"""
import ctypes
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
SRC = os.path.join(ROOT, "_pipeline")
IMAGES_DIR = os.path.join(ROOT, "assets", "images")    # 图片库真实位置（站点根相对 assets/images/）
LEGACY_IMAGES_DIR = os.path.join(ROOT, "images")       # 2026-11-04 之前的旧位置（只读不写）
CFG_PATH = os.path.join(SRC, "images_store.json")

STATE_LOCAL = "local"
STATE_ATTACHED = "attached"
STATE_DETACHED = "detached"
STATE_BROKEN = "broken"
STATE_INCREMENTAL = "incremental"

MODE_LOCAL = "local"
MODE_EXTERNAL = "external"
MODE_INCREMENTAL = "incremental"

# 退出码：4=盘没插（调用方按「跳过」处理，不算失败）；5=状态破损（必须人工处置）
EXIT_DETACHED = 4
EXIT_BROKEN = 5

FILE_ATTRIBUTE_REPARSE_POINT = 0x0400

DEFAULTS = {
    "version": 1,
    # local = 全部历史图都在本机；external = 整库外迁、本机只留 junction；
    # incremental = 历史图已被移到别的电脑（本机拿不到），本机 assets/images 只存增量新图
    "mode": "local",
    "external_root": None,        # 图片库现在的绝对路径（incremental 模式下可留空 = 位置未知）
    "junction": IMAGES_DIR,       # 本机 junction 路径（= assets/images，一般不用改）
    "detached_policy": "skip_image_steps",  # skip_image_steps | block_all
    "max_new_images_per_run": 3000,  # s04i 单轮下载上限（防全量重下）
    "prune_circuit_breaker": 50,     # s04f 单轮允许清理的 manifest 条目上限
    "note": "手改请确认 mode/external_root 成对出现",
}


def load_cfg():
    """读配置，缺字段用默认值补。文件不存在 = 搬运前状态 = mode local。"""
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            disk = json.load(f) or {}
    except (OSError, ValueError):
        disk = {}
    for k, v in disk.items():
        cfg[k] = v
    if not cfg.get("junction"):
        cfg["junction"] = IMAGES_DIR
    return cfg


def save_cfg(cfg):
    d = os.path.dirname(CFG_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with open(CFG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return CFG_PATH


def is_reparse(path):
    """Windows 下判断 junction / symlink（这些脚本历史代码全都不认 reparse，
    所以这里只是我们自己要看清楚状态，不改变任何行为）。"""
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    except Exception:
        return False
    return attrs != -1 and bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def link_target(path):
    t = None
    try:
        t = os.readlink(path)
    except (OSError, ValueError):
        return None
    # mklink /J 之后 os.readlink 可能返回扩展长度形式 \\?\D:\...，统一成普通盘符路径
    if t and t.startswith("\\\\?\\"):
        t = t[4:]
    return t


def volume_id(path):
    """卷序列号，用来判断图片库和项目是不是同一块卷（纯展示，不据此改变任何行为）。"""
    root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    try:
        serial = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), None, 0, ctypes.byref(serial), None, None, None, 0)
        if ok:
            return "%s:%08X" % (root[0], serial.value)
    except Exception:
        pass
    return root.lower()


def dir_stats(path, expect_files=None):
    """(文件数, 字节数)。目录不可达返回 (None, None)；空目录返回 (0, 0)。"""
    if not path or not os.path.isdir(path):
        return None, None
    n = b = 0
    for dp, dn, fn in os.walk(path):
        for f in fn:
            try:
                st = os.stat(os.path.join(dp, f))
            except OSError:
                continue
            n += 1
            b += st.st_size
    return n, b


def external_ok(cfg):
    """外置图片库是否可见且非空（**廉价判定**：只看能否列举到条目，
    不遍历 14 万个文件 —— 闸门每次导入都可能调用，必须快）。"""
    ext = cfg.get("external_root")
    if not ext:
        return False, "external_root 未配置"
    if not os.path.isdir(ext):
        return False, "图片库目录不存在：%s（库没接上？）" % ext
    try:
        it = os.scandir(ext)
    except OSError as e:
        return False, "图片库不可读：%s（%s）" % (ext, e)
    with it:
        for _ in it:
            return True, "可读：%s" % ext
    return False, "图片库目录为空：%s" % ext


def state(cfg=None):
    cfg = cfg or load_cfg()
    jn = cfg["junction"]
    ext_ready, ext_detail = external_ok(cfg)
    exists = os.path.isdir(jn)
    reparse = is_reparse(jn)
    mode = cfg["mode"]
    # 增量本机模式：历史图在别的电脑（可能永远拿不到），本机 assets/images 只存新图。
    # 若此时有人把库接回来并建了 junction，自动当作 attached 处理（回到历史行为）。
    if mode == MODE_INCREMENTAL:
        if reparse and ext_ready:
            return STATE_ATTACHED, cfg, ("mode=incremental 但检测到可达 junction，"
                                        "按外置库直通：%s" % ext_detail)
        n, _b = dir_stats(jn) if (exists and not reparse) else (0, 0)
        return STATE_INCREMENTAL, cfg, ("本机 assets/images 只存放增量新图（%d 个文件）；"
                                        "历史图片库在别的电脑，不参与本轮判定"
                                        % (n or 0))
    if mode != MODE_EXTERNAL:
        return STATE_LOCAL, cfg, "本机 assets/images 为真实目录（未外迁）"
    if reparse and ext_ready:
        return STATE_ATTACHED, cfg, "junction 可达：%s" % ext_detail
    if reparse and not ext_ready:
        return STATE_DETACHED, cfg, "junction 悬空（%s）" % ext_detail
    if exists and not reparse:
        n, _ = dir_stats(jn)
        if n:
            return STATE_BROKEN, cfg, ("本机 assets/images 是真实目录且有 %d 个文件，"
                                       "但配置为 external（疑似库不可达时跑过增量，需人工处置）" % n)
        return STATE_DETACHED, cfg, "本机存在空 assets/images 目录，图片库在本机之外"
    return STATE_DETACHED, cfg, "本机无 assets/images，图片库在本机之外（不可达或 junction 缺失）"


def new_images_only(cfg=None):
    """True = 历史图片不在本机可达位置。

    调用方据此改为「manifest 有条目就算已下载」：只下 manifest 里没有的 URL，
    绝不因为「本机 stat 不到文件」就把 13.8 万张历史图重下或把台账条目清掉。
    """
    st, _cfg, _msg = state(cfg)
    return st == STATE_INCREMENTAL


def ensure_junction(cfg=None, create=True):
    """自愈：mode=external 且盘已插、junction 缺失 → 重建 junction。
    绝不在 external 模式下创建**真实** images 目录（那正是全量重下的入口）；
    incremental 模式相反，它本来就要一个真实目录来接新图。"""
    cfg = cfg or load_cfg()
    if cfg["mode"] != MODE_EXTERNAL:
        return False, "mode=%s，无需 junction" % cfg["mode"]
    jn = cfg["junction"]
    ext = cfg.get("external_root")
    ready, detail = external_ok(cfg)
    if is_reparse(jn):
        return False, "junction 已存在"
    if os.path.isdir(jn):
        return False, "本机已有真实 assets/images 目录，拒绝覆盖（先人工处置）"
    if not ready:
        return False, "图片库不可用：%s" % detail
    if not create:
        return False, "未允许创建 junction"
    try:
        p = subprocess.run(["cmd", "/c", "mklink", "/J", jn, ext],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return False, "mklink 调用失败：%s" % e
    if is_reparse(jn):
        return True, "已重建 junction %s -> %s" % (jn, ext)
    return False, "mklink 失败：%s" % ((p.stdout or p.stderr or "").strip() or "rc=%d" % p.returncode)


def ensure_images_dir_at_import(cfg=None):
    """供 common.py 导入时调用（必须非致命）：
    local/incremental 建真实目录；external 只做 junction 自愈，不建真实目录。"""
    cfg = cfg or load_cfg()
    try:
        if cfg["mode"] == MODE_INCREMENTAL:
            # 增量本机模式：这个目录就是新增图片的落点，必须是真实目录。
            # （已存在 junction 时不动它，state() 会把它当 attached 处理）
            if os.path.isdir(IMAGES_DIR) and not is_reparse(IMAGES_DIR):
                return "incremental"
            if is_reparse(IMAGES_DIR):
                return "attached"
            os.makedirs(IMAGES_DIR, exist_ok=True)
            return "incremental"
        if cfg["mode"] != MODE_EXTERNAL:
            if not os.path.isdir(IMAGES_DIR):
                os.makedirs(IMAGES_DIR, exist_ok=True)
            return "local"
        if os.path.isdir(IMAGES_DIR) and not is_reparse(IMAGES_DIR):
            return "broken"      # 有真实目录，交给 guard 去拦，导入期不动它
        if not os.path.isdir(IMAGES_DIR):
            ok, _msg = ensure_junction(cfg)
            return "attached" if ok else "detached"
        return "attached"
    except OSError:
        return "error"


def guard(step, cfg=None, verbose=True):
    """图片相关步骤的统一闸门。返回 (ok, state, msg)。"""
    cfg = cfg or load_cfg()
    st, cfg, msg = state(cfg)
    if st in (STATE_LOCAL, STATE_ATTACHED, STATE_INCREMENTAL):
        return True, st, msg
    if st == STATE_DETACHED:
        msg = ("[%s] 图片库在本机之外且当前不可达 -> 跳过图片步骤"
               "（不下载、不清理 manifest）。%s" % (step, msg))
    else:
        msg = "[%s] 图片库状态破损：%s" % (step, msg)
    if verbose:
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
    return False, st, msg


def guard_or_exit(step, cfg=None):
    ok, st, _msg = guard(step, cfg)
    if ok:
        return st
    code = EXIT_DETACHED if st == STATE_DETACHED else EXIT_BROKEN
    print("STORE_%s %s" % ("DETACHED" if st == STATE_DETACHED else "BROKEN", step))
    sys.exit(code)


def image_steps_skipped(rc):
    """子进程退出码是不是「盘没插，跳过」而非真失败。"""
    return rc in (EXIT_DETACHED, EXIT_BROKEN)


def status_dict():
    cfg = load_cfg()
    st, cfg, detail = state(cfg)
    jn = cfg["junction"]
    if st in (STATE_LOCAL, STATE_ATTACHED, STATE_INCREMENTAL):
        n, b = dir_stats(jn)
        if st == STATE_INCREMENTAL and n is None:
            # 目录还没建（本模块自己不该在查询时创盘）：说清楚是「还没有新增图」
            n, b = 0, 0
    else:
        n, b = dir_stats(cfg.get("external_root"))
    return {
        "state": st,
        "mode": cfg["mode"],
        "new_images_only": st == STATE_INCREMENTAL,
        "junction": jn,
        "junction_is_reparse": is_reparse(jn),
        "junction_target": link_target(jn),
        "external_root": cfg.get("external_root"),
        "detached_policy": cfg["detached_policy"],
        "max_new_images_per_run": cfg["max_new_images_per_run"],
        "prune_circuit_breaker": cfg["prune_circuit_breaker"],
        "detail": detail,
        "images_files": n,
        "images_bytes": b,
    }


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    as_json = "--json" in sys.argv[1:]
    d = status_dict()
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    gb = d["images_bytes"]
    print("图片库状态 : %s（mode=%s）" % (d["state"], d["mode"]))
    print("详情       : %s" % d["detail"])
    print("junction   : %s  reparse=%s  target=%s"
          % (d["junction"], d["junction_is_reparse"], d["junction_target"]))
    print("外置图片库 : %s" % d["external_root"])
    if d["state"] == STATE_INCREMENTAL:
        print("下载口径   : 增量新图 only —— manifest 有条目即视为已下载，"
              "只下 manifest 里没有的 URL（不重下历史图、不清台账）")
    print("图片计数   : %s 个 / %s GB%s"
          % (d["images_files"], "%.2f" % (gb / 1024 ** 3) if gb else "-",
             "（本机仅增量新图）" if d["state"] == STATE_INCREMENTAL else ""))
    print("拔库策略   : %s" % d["detached_policy"])
    print("熔断阈值   : s04i 单轮≤%s 张，s04f 单轮清理≤%s 条"
          % (d["max_new_images_per_run"], d["prune_circuit_breaker"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
