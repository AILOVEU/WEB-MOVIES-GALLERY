# -*- coding: utf-8 -*-
"""把当前瀑布流画廊完整复刻到另一目录（离线可用 / 分发 / 备份）

复刻对象是本地画廊本身（index.html + data/data.js + assets/images/ + assets/screenshots/），
不是原站镜像——复制完成后双击目标目录的 index.html，即得到与源画廊
完全一致的瀑布流（含详情弹层、磁力复制、#m{id} 深链）。

用法:
  python _scripts/tools/replicate_gallery.py --to=<目标目录>            # 完整复刻（海报+剧照+数据）
  python _scripts/tools/replicate_gallery.py --to=<dir> --dry-run       # 只统计清单与体积, 不写文件
  python _scripts/tools/replicate_gallery.py --to=<dir> --no-shots      # 精简版: 不带剧照（体积大头）
  python _scripts/tools/replicate_gallery.py --to=<dir> --limit=100     # 只带最新 100 部（数据/海报/剧照同步裁剪）
  python _scripts/tools/replicate_gallery.py --to=<dir> --dir=<源画廊目录>  # 指定源画廊（默认同其他脚本）

特性:
  - 增量复制: 目标已存在且同大小的文件自动跳过, 可反复重跑
  - 复刻后自检: 回读目标 data/data.js, 校验每条记录的海报/剧照引用是否落地
"""
import os, re, shutil, sys, io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> _scripts/（common.py 所在）
from common import (gallery_dir, arg_val, arg_int, has_flag, load_datajs, save_datajs,
                    datajs_path, atomic_write, ASSETS_DIRNAME, IMAGES_DIRNAME,
                    SHOTS_DIRNAME, DATA_DIRNAME)

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SRC = gallery_dir()
DST = arg_val("--to=")
DRY = has_flag("--dry-run")
NO_SHOTS = has_flag("--no-shots")
LIMIT = arg_int("--limit=", None, "--limit")
if LIMIT is not None and LIMIT < 1:
    raise SystemExit("--limit 至少为 1（为 0 会生成空画廊）")

if not DST:
    raise SystemExit("缺少 --to=<目标目录> 参数")
DST = os.path.abspath(DST)
if os.path.normcase(os.path.abspath(SRC)) == os.path.normcase(DST):
    raise SystemExit("--to 不能与源画廊目录相同")

# index.html 中静态影片总数的 5 个锚点（title / meta / 徽标 / #cnt / 页脚）
COUNT_PATTERNS = (
    r"(<title>[^<]*?)(\d+)( 部影片离线画廊</title>)",
    r"(content=\"[^\"]*?共 )(\d+)( 部影片)",
    r"(<span class=\"n\">)(\d+)( 部</span>)",
    r"(共 )(\d+)( 部影片</div>)",
    r"(共收录 <b[^>]*>)(\d+)(</b>)",
)


def rewrite_counts(html, new_n):
    """只改写锚点处的影片总数（不用全文 \b数字\b）, 返回 (新 HTML, 命中处数)"""
    s = str(new_n)
    hit = 0
    for pat in COUNT_PATTERNS:
        html, k = re.subn(pat, lambda m, s=s: m.group(1) + s + m.group(3), html)
        hit += k
    return html, hit


records = load_datajs(datajs_path(SRC))
SRC_DATAJS = datajs_path(SRC)
print("source:", SRC)
print("records:", len(records), flush=True)

# 前置校验: 源画廊必须完整, 否则先报错而不是写完一半再崩
src_ih = os.path.join(SRC, "index.html")
if not os.path.exists(src_ih):
    raise SystemExit("源画廊缺少 index.html: %s\n  --to 是复刻成品站点, --dir 必须指向含 index.html 的画廊根目录" % src_ih)
if not os.path.exists(SRC_DATAJS):
    raise SystemExit("源画廊缺少 %s" % SRC_DATAJS)

# ---------------------------------------------------------------- 复制计划
# 项: (源绝对路径, 目标相对路径)
plan = []            # 散文件（root 文件 + subset 模式的图片）
copy_dirs = []       # 整目录复制（full 模式的 assets/images, assets/screenshots）
trim_records = None  # subset 模式下裁剪后的记录

root_files = [f for f in ("index.html", "RESUME.md", "%s/remote_urls.js" % DATA_DIRNAME)
              if os.path.exists(os.path.join(SRC, f))]

if LIMIT:
    n = LIMIT
    trim_records = records[:n]
    for r in trim_records:
        if r.get("p"):
            plan.append((os.path.join(SRC, *r["p"].split("/")), r["p"]))
        if not NO_SHOTS:
            for s in r.get("sh") or []:
                plan.append((os.path.join(SRC, *s.split("/")), s))
else:
    plan = [(os.path.join(SRC, f), f) for f in root_files]
    copy_dirs.append(("%s/%s" % (ASSETS_DIRNAME, IMAGES_DIRNAME),
                      os.path.join(SRC, ASSETS_DIRNAME, IMAGES_DIRNAME)))
    if not NO_SHOTS:
        copy_dirs.append(("%s/%s" % (ASSETS_DIRNAME, SHOTS_DIRNAME),
                          os.path.join(SRC, ASSETS_DIRNAME, SHOTS_DIRNAME)))


def dir_stats(d):
    n = size = 0
    for root, _, files in os.walk(d):
        for fn in files:
            fp = os.path.join(root, fn)
            n += 1
            size += os.path.getsize(fp)
    return n, size


if DRY:
    n_p = size_p = 0
    for fp, _ in plan:
        if os.path.exists(fp):
            n_p += 1
            size_p += os.path.getsize(fp)
    print("root files:", [f for f, _ in [(x, 0) for x in root_files]])
    print("loose files: %d (%.1f MB)" % (n_p, size_p / 1048576))
    for name, d in copy_dirs:
        if os.path.isdir(d):
            n, s = dir_stats(d)
            print("dir %-12s %d files (%.1f MB)" % (name + "/", n, s / 1048576))
        else:
            print("dir %-12s MISSING" % (name + "/"))
    if trim_records is not None:
        print("data.js trim -> %d records" % len(trim_records))
    print("(dry-run, 未写任何文件)")
    sys.exit(0)

# ---------------------------------------------------------------- 执行复制
os.makedirs(DST, exist_ok=True)
copied = skipped = missing = 0


def copy_one(sp, rel):
    """单文件增量复制（大小 + mtime 均相同才跳过，避免同名同尺寸不同内容漏更新）"""
    global copied, skipped, missing
    dp = os.path.join(DST, *rel.split("/"))
    if not os.path.exists(sp):
        missing += 1
        return
    if (os.path.exists(dp) and os.path.getsize(dp) == os.path.getsize(sp)
            and int(os.path.getmtime(dp)) == int(os.path.getmtime(sp))):
        skipped += 1
        return
    os.makedirs(os.path.dirname(dp), exist_ok=True)
    shutil.copy2(sp, dp)
    copied += 1


for f in root_files:
    copy_one(os.path.join(SRC, f), f)

for rel_src, d in copy_dirs:
    if not os.path.isdir(d):
        print("WARN: 目录不存在, 跳过:", d, flush=True)
        continue
    for root, _, files in os.walk(d):
        rel_dir = os.path.relpath(root, SRC).replace("\\", "/")
        for fn in files:
            copy_one(os.path.join(root, fn), rel_dir + "/" + fn)

for sp, rel in plan:
    copy_one(sp, rel)

print("files copied=%d skipped=%d missing=%d" % (copied, skipped, missing), flush=True)

# ---------------------------------------------------------------- data.js
tds = os.path.join(DST, DATA_DIRNAME, "data.js")
os.makedirs(os.path.dirname(tds), exist_ok=True)
if trim_records is not None:
    save_datajs(tds, trim_records)
    # index.html 里的静态总数只出现在 5 个锚点（title/meta/徽标/#cnt/页脚）；
    # 用锚点替换而非全文 \b数字\b，避免误改 CSS/JS 里的同值数字（如 width:100% / scrollY>600）
    ih = os.path.join(DST, "index.html")
    with open(ih, encoding="utf-8") as f:
        html = f.read()
    html, hit = rewrite_counts(html, len(trim_records))
    atomic_write(ih, html)
    print("data.js trimmed -> %d records, index.html 计数已同步 %d 处 (%.1f KB)"
          % (len(trim_records), hit, os.path.getsize(tds) / 1024), flush=True)
    if hit == 0:
        print("WARN: index.html 未命中任何计数锚点, 请手工核对静态总数", flush=True)
elif (not os.path.exists(tds)
      or os.path.getsize(tds) != os.path.getsize(SRC_DATAJS)):
    shutil.copy2(SRC_DATAJS, tds)
    print("data.js copied (%.1f MB)" % (os.path.getsize(tds) / 1048576), flush=True)
else:
    print("data.js unchanged", flush=True)

# ---------------------------------------------------------------- 自检
final = load_datajs(tds)
n_shots_ref = miss_p = miss_s = 0
for r in final:
    if r.get("p") and not os.path.exists(os.path.join(DST, *r["p"].split("/"))):
        miss_p += 1
    for s in r.get("sh") or []:
        n_shots_ref += 1
        if not os.path.exists(os.path.join(DST, *s.split("/"))):
            miss_s += 1

tot = sum(os.path.getsize(os.path.join(root, fn))
          for root, _, fs in os.walk(DST) for fn in fs)
print("VERIFY target: records=%d posters-missing=%d shots-ref=%d shots-missing=%d"
      % (len(final), miss_p, n_shots_ref, miss_s), flush=True)
print("DONE %s -> %.1f MB" % (DST, tot / 1048576), flush=True)
