# -*- coding: utf-8 -*-
"""把 `assets/videos/`、`assets/水果派/`、`assets/covers/` 搬去别的硬盘用的「清单导出 + 迁移后校验」工具。

要解决的两件事（就是用户提的两点）：
  1. **确保文件名单已经完整** —— `export` 会先跟记录对账（`videos_manifest.json` / `covers_manifest.json` 与
     `movies.js` 的 `talk`/`video`/`local` 字段），逐个文件确认「在册」，有任何一个文件没记录就报错并拒绝出清单；
     产物 `move_manifest.json`（机器校验用，data/state/）；人看的摘要**只打印到 stdout，不再落盘 .md**。
  2. **迁移后记录不会丢** —— 记录本来就和「文件在不在盘上」解耦（只增不删）：
     清单里有记录=已下载，不重下；`movies.js` 的 `video`/`talk`/`local` 是「曾经有、叫什么路径、多大」，
     文件搬走后 scan 只统计「已移走」不删字段。本工具**不改动任何数据**，只出清单和做校验。

命令：
  python move_check.py export                        # 对账 + 导出清单（迁移前必跑）
  python move_check.py status                        # 只看「记录 vs 目录实况」是否一致
  python move_check.py verify --at E:/JAVDB_media     # 拷到新盘后校验（缺/多/体积不符），--at 可以给新目录名
  python move_check.py verify --at E:/x --dir covers  # 只校验某一个目录（videos|水果派|covers）
说明：verify 用「文件名 + 字节数」比对（几十 GB 做哈希太慢，交给拷贝工具自己校验）。
      目录是递归扫的（`assets/covers/` 于 2026-11-03 改成 64 分桶，只数顶层会漏掉全部封面），
      比对键一律退到文件名，所以新盘上层级不同也能对上。
安全：只读这些目录与数据文件，只写 `data/state/move_manifest.json`；绝不删除或移动任何文件。
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
import datetime
import io
import json
import os
import sys

DATA = os.path.join(ROOT, "data")                        # 站点数据与名单
SITE = os.path.join(DATA, "site")    # 页面要读的 *.js（HTML 写死引用）
STATE = os.path.join(DATA, "state")  # 抓取状态：清单 / 终态名单 / 校验基准
ASSETS = os.path.join(ROOT, "assets")                        # 资源目录（covers / videos / 水果派）
WB = os.path.join(ROOT, "_pipeline")     # 过程中文件：断点 / cookie / 临时切片（删了会丢进度）
LOGS = os.path.join(ROOT, "_logs")      # 日志与验证截图：随时可删，不影响任何数据
MANIFEST_JSON = os.path.join(STATE, "move_manifest.json")
MOVIES_JS = os.path.join(SITE, "movies.js")
MV_VAR = "window.JAVDB_MOVIES = "

VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".webm", ".avi", ".mov", ".ts", ".flv", ".wmv", ".mpg", ".mpeg"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
# 已下载清单（判定「重不重下」的依据）：文件名 -> 记录（covers 清单的 key 是番号，取 rec["file"]）
RECORDS = {
    "videos": os.path.join(STATE, "videos_manifest.json"),
    "covers": os.path.join(STATE, "covers_manifest.json"),
}
# 目录 -> (movies.js 里挂的路径字段, 由哪份清单管「已下载」, 标签, 算数的文件扩展名)
DIRS = {
    "videos": {"field": "video", "manifest": "videos", "label": "预告片", "ext": VIDEO_EXT},
    "水果派": {"field": "talk", "manifest": None, "label": "解说", "ext": VIDEO_EXT},
    "covers": {"field": "local", "manifest": "covers", "label": "封面", "ext": IMAGE_EXT},
}


def log(*a):
    print(*a, flush=True)


# stdout 被管道/重定向时的两个 Windows 坑（GBK 编不动 ✅/⚠、提前关管道抛 Errno 22）
import win_utf8          # noqa: F401


def load_movies():
    t = io.open(MOVIES_JS, encoding="utf-8").read()
    i = t.index(MV_VAR) + len(MV_VAR)
    d, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
    return d


def load_records(which):
    """读「已下载清单」，返回 {小写文件名: 记录}。
    videos_manifest 的 key 就是文件名；covers_manifest 的 key 是番号，用 rec["file"]。
    ★ 一律用小写做键：Windows 大小写不敏感，不能拿字符串相等当「在册」依据。"""
    p = RECORDS[which]
    if not os.path.exists(p):
        return {}
    items = json.load(io.open(p, encoding="utf-8"))["items"]
    out = {}
    for k, v in items.items():
        name = v.get("file") or (k if "." in k else k + ".jpg")
        out[name.lower()] = v
    return out


def base(path_or_name):
    """清单里的 file 可能是 `桶/名字`（covers 分桶），比对一律退到文件名级别（小写）。"""
    return os.path.basename(str(path_or_name).replace("\\", "/")).lower()


def list_dir(d):
    """递归列出该目录下的目标文件。

    ⚠ 必须递归：`assets/covers/` 是 64 分桶结构（顶层只有 0..63 子目录），
    早先用 os.listdir 只数顶层 → status 报「0 个文件」、export 出的清单会漏掉全部封面。
    file 存「相对 d 目录的正斜杠路径」（如 `12/ABP-043.jpg`）；扁平目录仍是纯文件名。
    others 顺带列出同目录的非目标文件（提醒一起搬）。"""
    p = os.path.join(ASSETS, d)
    if not os.path.isdir(p):
        return None, [], []
    ext = DIRS[d]["ext"]
    items, others = [], []
    for root, _dirs, files in os.walk(p):
        rel_dir = os.path.relpath(root, p).replace("\\", "/")
        for name in files:
            rel = name if rel_dir == "." else rel_dir + "/" + name
            try:
                st = os.stat(os.path.join(root, name))
            except OSError:
                continue
            if os.path.splitext(name)[1].lower() in ext:
                items.append({"file": rel, "size": st.st_size,
                              "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
            else:
                others.append({"file": rel, "size": st.st_size})
    items.sort(key=lambda x: x["file"])
    others.sort(key=lambda x: x["file"])
    return p, items, others


def audit(d, items, movies, recs):
    """对账：每个文件是否都在册（movies.js 有对应路径字段；清单管的目录还额外要有记录）。"""
    field = DIRS[d]["field"]
    mounted = {}
    for code, e in movies.items():
        v = e.get(field)
        if v:
            mounted[base(v)] = code      # 大小写不敏感、层级无关（分桶后路径带子目录）
    # 清单里已标 valid=false 的（当年下到的是拦截页/CDN 真没有）本来就不该挂 local，
    # 不能当成「在册未挂」的错（它们由下面 invalid_content 单独提醒）。
    invalid_keys = {f for f, r in (recs or {}).items() if r.get("valid") is False}
    not_mounted = [it["file"] for it in items
                   if base(it["file"]) not in mounted and base(it["file"]) not in invalid_keys]
    # 未挂字段里分两类：番号确实在库里（应补挂，算错）/ 番号不在库里（旧数据残留，只提醒）
    orphans = [f for f in not_mounted if os.path.splitext(os.path.basename(f))[0].upper() not in movies]
    real_unmounted = [f for f in not_mounted if f not in orphans]
    # 播放/封面字段指向本目录、但目录里已经没有文件（=已移走的记录，属正常，不算错）
    url_prefix = "assets/" + d + "/"
    dangling = sorted(code for code, e in movies.items()
                      if (e.get(field) or "").startswith(url_prefix)
                      and not os.path.exists(os.path.join(ROOT, e[field].replace("/", os.sep))))
    missing_rec = [it["file"] for it in items if recs is not None and base(it["file"]) not in recs]
    # 清单里登了但内容坏了（封面 valid=false = 下到的其实是拦截页/残缺）——搬之前应先修
    have_names = {base(it["file"]) for it in items}
    invalid = sorted(f for f, r in (recs or {}).items() if r.get("valid") is False and f in have_names)
    return {"mounted": len(mounted) - len(not_mounted), "not_mounted": real_unmounted,
            "orphan_files": orphans,
            "missing_record": missing_rec, "invalid_content": invalid,
            "records_of_moved_files": len(dangling),
            "bytes": sum(it["size"] for it in items)}


def cmd_status(_args=None):
    movies = load_movies()
    bad = 0
    for d in DIRS:
        which = DIRS[d]["manifest"]
        recs = load_records(which) if which else None
        _p, items, others = list_dir(d)
        if items is None:
            log(f"{d}/ 目录不存在")
            continue
        a = audit(d, items, movies, recs)
        flag = "✅" if not a["not_mounted"] and not a["missing_record"] else "❌"
        bad += len(a["not_mounted"]) + len(a["missing_record"])
        log(f"{flag} {d}/（{DIRS[d]['label']}）：{len(items)} 个文件 / {a['bytes'] / 1073741824:.2f} GB | "
            f"已挂 `{DIRS[d]['field']}` {a['mounted']} | 在册未挂 {len(a['not_mounted'])} | "
            + (f"清单缺记录 {len(a['missing_record'])} | " if recs is not None else "")
            + (f"内容非图片 {len(a['invalid_content'])} | " if a["invalid_content"] else "")
            + f"指向已移走文件的记录 {a['records_of_moved_files']} 条（正常保留）")
        for f in a["not_mounted"][:10]:
            log(f"    未挂路径字段：{f}")
        for f in a["missing_record"][:10]:
            log(f"    不在已下载清单里：{f}")
        if a["invalid_content"][:10]:
            log(f"    ⚠ 内容不是图片 {len(a['invalid_content'])} 张（CDN 上确实没有或当年下到拦截页；不影响搬迁，"
                "但页面会退远程图/占位图）：" + ", ".join(a["invalid_content"][:10])
                + " ……要重下：python _script/manifest/covers_check.py --repair --bad-only --retry-unavailable")
        if others:
            log(f"    同目录其它文件（建议一起搬）：{', '.join(o['file'] for o in others[:5])}")
        if a["orphan_files"]:
            log(f"    番号不在 movies.js 里的旧文件 {len(a['orphan_files'])} 个（不算错，跟着一起搬即可）："
                + ", ".join(a["orphan_files"][:5]) + (" ..." if len(a["orphan_files"]) > 5 else ""))
    return 0 if bad == 0 else 1


def cmd_export(args):
    rc = cmd_status()
    if rc and not args.force:
        log("\n❌ 对账未通过：上面这些文件还没有记录。先跑对应的 scan 再来："
            "videos/ → `python _script/videos_manifest.py scan` + `python _script/media_scan.py`；"
            "covers/ → `python _script/covers_manifest.py scan`（坏图先 `covers_check.py --repair --bad-only`）。"
            "（确认无误可加 --force 强行出清单）")
        return rc
    movies = load_movies()
    payload = {"version": 1,
               "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "purpose": "videos/、水果派/、covers/ 迁移到其他硬盘用的完整文件清单与校验基准",
               "how_to_verify": "python _script/move_check.py verify --at <新盘上的路径>",
               "dirs": {}}
    for d in DIRS:
        which = DIRS[d]["manifest"]
        recs = load_records(which) if which else None
        _p, items, others = list_dir(d)
        if items is None:
            continue
        a = audit(d, items, movies, recs)
        field = DIRS[d]["field"]
        mounted = {base(e[field]): code for code, e in movies.items() if e.get(field)}
        payload["dirs"][d] = {
            "label": DIRS[d]["label"],
            "source_path": os.path.join(ASSETS, d).replace("\\", "/"),
            "files": len(items),
            "bytes": a["bytes"],
            "playback_field": field,
            "all_mounted": not a["not_mounted"],
            "items": [{**it, "code": mounted.get(base(it["file"]), "")} for it in items],
            "extra_files": others,
        }
        s = payload["dirs"][d]
        log(f"记录 {d}/：{s['files']} 个文件 / {s['bytes'] / 1073741824:.2f} GB，播放字段全部在册={s['all_mounted']}")
    with io.open(MANIFEST_JSON, "w", encoding="utf-8") as f:
        # 分桶后 covers 有 5 万+ 条，indent 会把基准文件撑到十几 MB；这份只给机器读，紧凑存
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    lines = ["# videos/ 、水果派/、covers/ 迁移清单（自动生成，勿手改）", "",
             f"- 生成时间：{payload['generated_at']}",
             "- 校验基准文件：`move_manifest.json`（配合 `python _script/move_check.py verify --at <新路径>`）", ""]
    for d, s in payload["dirs"].items():
        lines += [f"## {d}/（{s['label']}）", "",
                  f"- 源路径：`{s['source_path']}`",
                  f"- 文件：**{s['files']} 个**，合计 **{s['bytes'] / 1073741824:.2f} GB**",
                  f"- 路径字段 `{s['playback_field']}` 是否全部在册：{'✅ 是' if s['all_mounted'] else '❌ 有缺'}",
                  f"- 同目录其它文件：{', '.join(o['file'] for o in s['extra_files']) or '无'}", ""]
    log(f"\n校验基准已写出：{MANIFEST_JSON}")
    print("\n".join(lines))        # 可读摘要只打印，不再多养一份会过期的 .md
    return 0


def cmd_verify(args):
    if not os.path.exists(MANIFEST_JSON):
        log("没有 move_manifest.json，请先：python _script/move_check.py export")
        return 2
    mf = json.load(io.open(MANIFEST_JSON, encoding="utf-8"))
    at = args.at.rstrip("/\\")
    if not os.path.isdir(at):
        log("目标路径不存在：", at)
        return 2
    allok = True
    for d, s in mf["dirs"].items():
        if args.dir and args.dir != d:
            continue
        # --at 可以给资源根（下面分 videos/covers/水果派 子目录），也可以直接给该目录本身；
        # 判据用「有没有同名子目录」，不再靠顶层是否有文件（分桶后 covers 顶层只有 0..63 子目录）
        sub = os.path.join(at, d)
        tgt = sub if os.path.isdir(sub) else at
        if not os.path.isdir(tgt):
            log(f"❌ {d}/：在 {at} 下没找到对应目录")
            allok = False
            continue
        have = {}
        for root, _dd, files in os.walk(tgt):
            for name in files:
                try:
                    have[base(name)] = os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        want = {base(it["file"]): it["size"] for it in s["items"]}
        extra_names = {base(o["file"]) for o in s.get("extra_files", [])}
        missing = sorted(set(want) - set(have))
        extra = sorted(set(have) - set(want) - extra_names)
        mismatch = sorted(f for f in set(want) & set(have) if want[f] != have[f])
        ok = not missing and not mismatch
        allok = allok and ok
        log(f"{'✅' if ok else '❌'} {d}/ -> {tgt}")
        log(f"   清单 {len(want)} 个 / {sum(want.values()) / 1073741824:.2f} GB"
            f" | 实际 {len(have)} 个 / {sum(have.values()) / 1073741824:.2f} GB"
            f" | 缺失 {len(missing)} | 体积不符 {len(mismatch)} | 清单外多出的 {len(extra)}")
        for f in missing[:10]:
            log(f"     缺：{f}")
        for f in mismatch[:10]:
            log(f"     体积不符：{f}（清单 {want[f]} / 实际 {have[f]}）")
        if extra[:5]:
            log(f"     额外（不算错，供参考）：{', '.join(extra[:5])}")
    log("\n校验通过：可以删除原目录" if allok else "\n校验未通过：**先别删原目录**，把上面缺的补拷完再验一次")
    return 0 if allok else 1


def main():
    ap = argparse.ArgumentParser(description="videos/ 与 水果派/ 的迁移清单与校验")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("export").add_argument("--force", action="store_true")
    sub.add_parser("status")
    pv = sub.add_parser("verify")
    pv.add_argument("--at", required=True, help="新盘上的路径（根目录或具体目录都行）")
    pv.add_argument("--dir", default="", choices=["", "videos", "水果派", "covers"], help="只校验某一个目录")
    args = ap.parse_args()
    cmd = args.cmd or "status"
    if cmd == "status":
        return cmd_status()
    if cmd == "verify":
        return cmd_verify(args)
    return cmd_export(args)


if __name__ == "__main__":
    sys.exit(main())
