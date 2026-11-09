# -*- coding: utf-8 -*-
r"""增量"新增更新"总控：每次刷 10 页，只抓列表里没见过的新帖，已存在的旧帖不重抓/不更新，
新帖插到列表最前面，累积成越来越全的离线库。

站点列表按热度随机刷新，所以每轮流程：
  0) external_store.py write-guard —— 本机 assets/images/ 目录就位（缺了自动重建，新图全落这里）
  0.5) backup.py snap —— 先留一份可回退快照到 _backup/YYYYMMDD/（写库前最后一道保险），并顺手
       prune 掉超过 14 天的旧快照
  1) crawl.py list --force   —— 强制重抓 10 页列表（列表每次都变）
  2) crawl.py threads        —— 只抓"当前列表 - 已抓完(crawl_done)"的差集 = 新帖详情(前5页评论)
  3) parse.py                —— 先解一遍，产出 parsed/（refs 要知道正文里引用了哪些帖）
  3.5) crawl.py refs         —— 补抓"帖内引用"：正文/评论里提到的本站 tid，**逐层闭包**（默认最多
                               --ref-depth 10 层，每层后自动补跑 parse，收敛就停；实测一阶 88 个 tid）
  4) parse.py                —— 再跑一次：累积 _pipeline/index.json（列表新帖插最前、引用帖排末尾、
                               已存在旧帖原样不动）+ 生成 parsed/
  5) images.py all --only=   —— 只下载本轮新增帖的图（含引用帖），落到本机 assets/images/
                               （已存在的按魔术字节跳过）
  6) build.py                —— 重建 data/data.js(累积有序, 含 window.SHARD_N) + data/posts/shard_*.js 详情分片（tid%SHARD_N 归片，含帖内链接改站内）
  7) validate.py             —— 校验：新图必须本机可见；旧图按 imgmap 台账判"已人工外迁"；JS 语法
  8) guard.py diff pre post  —— 回归基准：证明本轮**只新增、历史没动**（旧帖不被改写/不重排、
     imgmap 台账不缩水、原始件不减少）；基准快照在真正要写库前先 snap pre

**复制到移动硬盘不在这个脚本里**（2026-10-30 定，2026-11-03 补充）：每轮跑完由用户自己把整份 `JAVBUS` 文件夹
复制到移动硬盘的站点目录（覆盖，硬盘保留全量图片），再删本机 `assets/images/`。硬盘那份也是一份完整可打开的
站点，所以图片必须落在站点内的 `assets\images\`，不是硬盘根上的 `images\`（详见 _scripts/EXTERNAL_STORE.md §3.1）。
下一轮从"图片目录不存在"开始，write-guard 会重建空目录，照常只下新图。

用法：
  python update.py                       # 默认 10 页、每帖前 5 页，新帖正文+图片全部落本机
  python _scripts/inc/update.py          # 分区后的实际路径（子脚本由 paths.script() 按名字找）
  python update.py --pages=12 --max-pages=5   # 页数一次贯穿 crawl list / crawl threads / parse 三步
  python update.py --skip-images         # 只要文字，本轮不下图
cookie 失效(登录墙)时 crawl 会以 rc=2 退出，本脚本据此停止并提示重抽 cookie。
目录布局见 _scripts/common/paths.py：原始 HTML 在 _rawdata/，过程与台账在 _pipeline/，日志与验证截图在 _logs/
（你重定向的 *.log 也放这里，整个 _logs/ 删掉不丢任何数据）。
日志命名铁规：_logs/ 里的文件名必须带 `YYYYMMDD_` 日期前缀（脚本产出由 paths.dated() 自动加），
每轮开头会把没带前缀的文件列出来提醒。
"""
import argparse
import io
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))                    # _scripts/inc
sys.path.insert(0, _HERE)                                             # 同区的 parse.py
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "common"))     # 公共库：paths.py
import parse as P          # 名字 P 已被 parse 占用，paths 只能用别名 PT
import paths as PT

ROOT = PT.ROOT
LAST_NEW = PT.LAST_NEW     # 过程区：本轮新增 tid
PY = sys.executable or "python"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def run(script, *args):
    """调兄弟分区的脚本：按名字走 paths.script() 定位，以后再挪分区不用改这里。"""
    cmd = [PY, PT.script(script)] + list(args)
    print("\n$ " + " ".join([os.path.relpath(cmd[1], PT.ROOT).replace("\\", "/")] + list(args)), flush=True)
    r = subprocess.run(cmd, cwd=PT.ROOT)
    return r.returncode


def read_last_new():
    if os.path.exists(LAST_NEW):
        return [x.strip() for x in io.open(LAST_NEW, encoding="utf-8").read().split("\n") if x.strip()]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=10)
    ap.add_argument("--max-pages", type=int, default=5)
    ap.add_argument("--ref-depth", type=int, default=10,
                    help="帖内引用的逐层闭包上限（crawl.py refs 内部自己每层后补跑 parse；收敛就停）")
    ap.add_argument("--skip-images", action="store_true")
    ap.add_argument("--hdd", action="store_true",
                    help="已废弃：复制到移动硬盘由人工执行，本脚本不再自动外迁（加了也不做任何事）")
    ap.add_argument("--allow-local-images", action="store_true",
                    help="已废弃：新图现在默认就落本机 images/（加了也不做任何事）")
    ap.add_argument("--dry-run", action="store_true", help="只刷列表并报告将新增多少帖，不抓详情/图")
    a = ap.parse_args()

    if a.hdd or a.allow_local_images:
        print("[update] 提示：复制到移动硬盘 + 删本机 assets/images/ 已改为你人工执行，这两个旗标不再起作用。")

    # 本机 assets/images/ 就位：缺了就重建（上一轮你把整份文件夹复制走、删了本机图片目录是正常起点），
    # 新帖图片一律落这里。只有残留旧策略的目录联接(rc=4)或建不出目录(rc=5)才停。
    rc = run("external_store.py", "write-guard")
    if rc != 0:
        print("\n[update] 已停止：images 目录未就位（rc=%d），按上面的提示处理后重跑。" % rc)
        sys.exit(rc)

    # 日志命名约定：_logs/ 里的文件必须带 YYYYMMDD_ 前缀（只提醒，不阻断本轮）
    viol = PT.logs_violations()
    if viol:
        print("\n[update] 提醒：_logs/ 有 %d 个文件名没带 YYYYMMDD_ 日期前缀（约定见 _scripts/common/paths.py）："
              % len(viol))
        for v in viol[:10]:
            print("           -", v)
        if len(viol) > 10:
            print("           ...共 %d 个" % len(viol))

    # 写库前先留一份按日期的快照（_backup/YYYYMMDD/），挂了轮也能回退
    if run("backup.py", "snap") != 0:
        print("\n[update] 已停止：备份没写成，不动现有数据。"); sys.exit(1)

    rc = run("crawl.py", "list", "--force", "--pages=%d" % a.pages)
    if rc == 2:
        print("\n[update] 登录墙：cookie 失效，请更新 _pipeline/cookie.txt 后重试。"); sys.exit(2)
    if rc != 0:
        print("\n[update] 列表抓取失败 rc=%d" % rc); sys.exit(rc)

    if a.dry_run:
        current = P.build_index(pages=a.pages)
        _, new_tids = P.merge_store(current, P.load_store())
        print("\n[update] DRY-RUN：本次列表 %d 帖，将新增 %d 帖（详情/图片未抓取，index.json 未改）。"
              % (len(current), len(new_tids)))
        if new_tids:
            print("[update] 新增 tid 示例:", ", ".join(new_tids[:12]) + (" ..." if len(new_tids) > 12 else ""))
        return

    rc = run("guard.py", "snap", "pre")      # 基准：要开始写库了，先拍一份当前状态
    if rc == 2:
        print("\n[update] 已停止：guard 基准拍不下（快照目录不可写？）。"); sys.exit(rc)

    # 本轮"全部新增"的基准必须在**第一次 parse 之前**取（2026-11-06 修）。
    # 原来这行写在下面 parse.py 之后，而 parse 已经把列表新帖并进 index.json 了，
    # 于是 added 只剩引用闭包补录的那几个帖：2026-11-06 实测列表新增 39 + 引用补录 7，
    # added 却只有 7 —— images.py --only 与 validate --new 跟着漏掉 39 帖的图片，
    # 收尾结论还把"入库 46 帖"报成"7 帖"。added 同时决定这三步的范围，必须先取。
    before = {str(r["tid"]) for r in P.load_store()}

    rc = run("crawl.py", "threads", "--pages=%d" % a.pages, "--max-pages=%d" % a.max_pages)
    if rc == 2:
        print("\n[update] 登录墙：cookie 失效，已抓部分已存盘；更新 cookie 后重跑即可续抓。"); sys.exit(2)

    rc = run("parse.py", "--pages=%d" % a.pages)         # 先解一轮：refs 依赖 parsed 提引用
    rc = run("crawl.py", "refs", "--max-pages=%d" % a.max_pages, "--max-depth=%d" % a.ref_depth)
    if rc == 2:
        print("\n[update] 登录墙：cookie 失效，已抓部分已存盘；更新 cookie 后重跑即可续抓。"); sys.exit(2)
    run("parse.py", "--pages=%d" % a.pages)              # 收尾再 parse 一次（crawl refs 内部已逐层 parse，这里是保险）
    after = {str(r["tid"]) for r in P.load_store()}
    # 图片范围要的是“本轮全部新增”（列表新增 + 各层引用），last_new 只有最后一层，不够用
    added = sorted(after - before) or read_last_new()

    # 图片只下本轮新增帖：images.py 不带 --only 时会全量重扫 parsed 下所有 URL，
    # 连历史确认拿不到的死链也要重试 3 次 —— 增量范围必须收窄到新增。
    if not a.skip_images:
        if added:
            run("images.py", "all", "--workers=5", "--only=" + ",".join(added))
        else:
            print("\n[update] 本次无新增帖，跳过图片步骤（不做全量重扫）")
    run("build.py")
    # 把“本轮全部新增”显式传给 validate：last_new.txt 在闭包补抓后只剩最后一层
    vargs = ["--new=" + ",".join(added)] if added else []
    vrc = run("validate.py", *vargs)
    run("guard.py", "snap", "post")
    grc = run("guard.py", "diff", "pre", "post")
    run("backup.py", "prune", "--keep=14")

    print("\n" + "=" * 56)
    print("[update] 完成：本次入库 %d 帖（列表新帖插最前、帖内引用补录排末尾），累积库见 data/data.js。" % len(added))
    if added:
        print("[update] 新增 tid 示例:", ", ".join(added[:12]) + (" ..." if len(added) > 12 else ""))
    if vrc != 0:
        print("[update] 注意：validate 判 FAIL —— 有图片既不在本机也不在 imgmap 台账里，先按上面 MISSING 清单排查。")
    if grc != 0:
        print("[update] 注意：guard 判不通过 —— 本轮动到了历史（旧帖被改写/重排、台账缩水、原始件减少），"
              "先按上面 `!` 清单查清楚再做硬盘收尾！")
    else:
        print("[update] 回归基准通过：只新增，历史未动。")
    if vrc == 0 and grc == 0:
        print("[update] 校验通过。接下来请你人工收尾：把整份 JAVBUS 复制到移动硬盘的站点目录（覆盖，"
              "别删硬盘已有图片），确认后删本机 assets/images/。")
        print("[update] 硬盘侧的图必须落在站点内的 assets\\images\\<tid>\\（不是硬盘根上的 images\\），"
              "否则硬盘那份站点的旧图会全破；一次性归位命令见 _scripts/EXTERNAL_STORE.md §3.1。")
    print("=" * 56)


if __name__ == "__main__":
    main()
