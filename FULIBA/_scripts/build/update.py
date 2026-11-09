# -*- coding: utf-8 -*-
"""
update.py — 增量更新（原站新增/更新文章时用）

流程：
  基线计数 → s01 拉 sitemap 合并 urls.json → s01b 翻分页列表页补漏
  → 算新增 →（默认）只处理新增
  → s02 抓 HTML（已有跳过）→ s03 解析（已有跳过）→ s04i 下图（manifest 命中跳过）
  → s04l 代理地址还原 → s04f 重建 manifest → s04d 回写 → s06 建站 → _verify
  → 计数校验报告（只展示，不阻断）

用法:
  _scripts\\py.cmd _scripts\\build\\update.py              # 只比对当年（默认），含 s01b 各归档第 1 页快扫
  _scripts\\py.cmd _scripts\\build\\update.py --dry        # 只算增量，不抓不写（s01b 也只算差集）
  _scripts\\py.cmd _scripts\\build\\update.py --force      # 无新增也重建站点（并跑完整流水线，含下图）
  _scripts\\py.cmd _scripts\\build\\update.py --refresh    # 同时刷新 lastmod 变化的已有文章（自动备份）
  _scripts\\py.cmd _scripts\\build\\update.py --deep       # s01b 穷举全部分页（首页 372 页 + 28 分类，首次补漏用）
  _scripts\\py.cmd _scripts\\build\\update.py --pages=N    # 只处理前 N 篇新增（试跑）
  _scripts\\py.cmd _scripts\\build\\update.py --years=2026,2027   # 自定义比对年份（2025-12-31 跨年后还能补前一年）
  _scripts\\py.cmd _scripts\\build\\update.py --years=all  # 全年份比对（逃生阀，相当于年度化前的行为）

设计要点:
1. 不改动已有页面：s06 用 url_key 生成文件名，旧文章路径与内容不变（幂等覆盖）。
2. --refresh 采用「先移入 _backup/update_refresh/<日期>/，再重抓」：抓取失败的自动移回，不会丢数据。
3. 计数校验是**提示性质**：差异只写进报告与屏幕，不改变退出码，由人决定后续动作。
4. 全量耗时参考：s06 建站约 3 分钟、s04f 重建 manifest 约 1~2 分钟，其余秒级。
5. sitemap 与分页列表页是两套发现通道，集合并不互相包含：只跑 s01 会漏掉
   「分页里有、sitemap 里没有」的文章，故 s01b 默认参与增量（--quick 只翻各归档
   第 1 页，~29 请求 / 40 秒）；深层归档页由 _rawdata/pages/ 缓存兜住，不重复请求。
6. 图片库位置见 §13（store.py）。**incremental 模式**下历史图已被移到别的电脑：
   只下 manifest 里没有的新图到本机 assets/images/，历史引用与 manifest 台账一律不动。
7. **范围模式（2026-11-04）**：有新增时，本轮 todo 的 URL 会写成一份
   `_pipeline/<日期>/update_keys.json`，s02/s02b/s03/s04i/s04f/s04d 用 `--keys-file=`
   接住、只处理这批，s06 按触及年份局部重建。以前这些步骤都会把整个库重读一遍，
   只为确认「另外七千篇不用动」（实测一轮 614 秒里约 380 秒花在这上面）。
   **--force = 全树档**（不写范围文件、不传 --keys-file）；rebuild.py --all /
   archive.py / 手工单跑也都不传，它们的全树兜底与半成品自愈一字不变。
   _verify 永远全树：它是范围模式的回归门（少写一处回写会被它报出来）。
"""
import os
import sys
import time
import shutil

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C
import rebuild as R

URLS = os.path.join(C.SRC, "urls.json")
PAGES_PENDING_NAME = "urls_pages_pending.json"   # 逐轮累积的滚动快照，读最近一份（_pipeline/<日期>/）
REPORT_NAME = "update_report.txt"   # 每轮回报：写 _logs/<当天>/（跟其他日志同口径）
# --refresh 的可逆点：按日期落在 _backup 下（与「大改按日期建目录」的口径一致）
BK = os.path.join(C.BACKUP_DIR, "update_refresh", time.strftime("%Y%m%d"))

STEPS = [
    # 注：s01b 不在这里 —— 它由主流程直接调（需要 --quick/--deep 开关），
    # 放在这里会被 run_step 无参数重跑一遍（= 全量重翻 878 页）。
    # 路径都写相对 _scripts/ 的，跟 rebuild.ALL 同一口径。
    ("s02", "collect/s02_fetch_html.py"),
    ("s02b", "collect/s02b_fetch_pagenav.py"),
    ("s03", "parse/s03_parse.py"),
    ("s04i", "images/s04i_fetch_body_fast.py"),
    ("s04l", "images/s04l_proxy_unwrap.py"),
    ("s04f", "images/s04f_rebuild_manifest.py"),
    ("s04d", "images/s04d_rewrite.py"),
    ("s06", "build/s06_build_site.py"),
    ("verify", "audit/_verify.py"),
]

# 范围模式的全套工具（SCOPE_STEPS / step_extra / touched_years）住在流水线层 rebuild.py，
# 与 archive.py（移出归档重抓）共用一份定义；这里只负责写范围文件与把参数递下去。


# 图片库未挂载时必须整组跳过的步骤（顺序：下图 → 还原 → 重建 manifest → 回写）。
# 让它们在没有图片库的状态下跑，等于授权「本机全量重下 + 抹掉 manifest 台账」。
IMAGE_STEPS = ("s04i", "s04l", "s04f", "s04d")


def parsed_keys():
    """已有解析产物的 key 集合（只读年度容器的 meta 行，不吞正文）。

    这一步是差集的判据，本来就要看全树，所以只能降常数：七千个文件串行 open
    实测 8~10 秒，改成 16 线程读 meta 行后 ~2 秒（语义一字不变，只是并发）。
    """
    from concurrent.futures import ThreadPoolExecutor
    paths = C.Y.paths()
    keys = set()
    if paths:
        with ThreadPoolExecutor(max_workers=16) as ex:
            metas = list(ex.map(C.Y.read_meta, paths))
        for p, m in zip(paths, metas):
            if m and m.get("has_parsed"):
                base = os.path.basename(p)
                keys.add(base[:-len(C.Y.MATERIAL_SUFFIX)])
    if keys:
        return keys
    # 兜底：容器没有 meta 标记时（早期转换产物）回退到真读 parsed
    for _url, key, _slot, _a in C.Y.iter_parsed():
        keys.add(key)
    return keys


def parsed_lastmod(keys):
    """读已解析容器的 lastmod，仅 --refresh 时使用。"""
    out = {}
    for url, key, _slot, a in C.Y.iter_parsed():
        if key in keys:
            out[key] = a.get("lastmod") or ""
    return out


def move_to_backup(keys):
    """把待刷新的**容器文件**整体移入备份区，返回移动记录用于失败回滚。

    年度化后一篇就是一个文件，不再存「html + parsed 两处」；容器拿掉后
    s02 会重新抓 HTML、s03 重新解析，语义与旧版一致。
    """
    os.makedirs(BK, exist_ok=True)
    moved = []
    for k in keys:
        _slot, src = C.Y.locate(k)
        if src and os.path.exists(src):
            dst = os.path.join(BK, os.path.basename(src))
            try:
                shutil.move(src, dst)
                moved.append((src, dst))
            except OSError:
                pass
    C.Y.forget_index()
    return moved


def rollback(moved):
    n = 0
    for src, dst in moved:
        if not os.path.exists(src) and os.path.exists(dst):
            try:
                os.makedirs(os.path.dirname(src), exist_ok=True)
                shutil.move(dst, src)
                n += 1
            except OSError:
                pass
    return n


def write_report(lines):
    path = C.log_path(REPORT_NAME)          # _logs/<今天>/update_report.txt
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("[报告] %s" % path)


def main():
    dry = "--dry" in sys.argv
    force = "--force" in sys.argv
    refresh = "--refresh" in sys.argv
    deep = "--deep" in sys.argv
    pages = None
    yarg = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--years=")), None)
    years = C.Y.parse_years_arg(yarg) if yarg else None
    full_year = (not years) or len(years) >= len(C.Y.all_slots())
    yflag = None if (not years or full_year) else ["--years=" + ",".join(years)]
    for a in sys.argv[1:]:
        if a.startswith("--pages="):
            pages = int(a.split("=", 1)[1])

    out = []

    def say(s=""):
        out.append(s)
        print(s)

    t_all = time.time()
    say("=" * 66)
    say("FULIBA 增量更新  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    say("=" * 66)

    base = R.counts()
    say("[基线] urls=%s 容器=%s(原始%s/解析%s) 子页=%s 单篇页=%s data.js=%s images=%s"
        % (base["urls"], base["material"], base["html"], base["parsed"],
           base["subpages"], base["articles"], base["datajs"],
           "未挂载" if base["images"] is None else base["images"]))
    say("[比对范围] %s"
        % ("默认：s01 只比对当年（%s），往年月片不拉不解析（--years=… 可改，--years=all 全量）"
           % time.strftime("%Y") if not yarg else
           ("全部槽位（--years=all）" if full_year else ",".join(years))))

    # ---------- 0) 图片库位置（外迁后 images 在移动硬盘，本机只有 junction） ----------
    st, scfg, sdetail = C.store_state()
    say("[图片库] state=%s mode=%s 目标=%s ｜ %s"
        % (st, scfg["mode"], scfg.get("external_root"), sdetail))
    if st == "incremental":
        say("        增量本机模式：历史图在别的电脑（可能拿不到），本轮**只下载 manifest 里没有的新图**，")
        say("        落到本机 images/；历史引用一条不改（库接回来自动生效），manifest 台账不清。")
    offline = st in ("detached", "broken")
    if st == "broken":
        say("[中止] 本机 images 是真实目录且非空，但配置 mode=external。")
        say("        这是「拔盘跑过增量、图往本机重下」的现场，不做任何写入。")
        say("        处置：插上移动硬盘 -> _scripts\\py.cmd _scripts\\store.py --status 看清状态 ->")
        say("        人工比对/合并两份图（哈希同名，可直接覆盖到移动硬盘），再删本机真实目录。")
        write_report(out)
        return C.ST.EXIT_BROKEN
    if offline and scfg.get("detached_policy") == "block_all":
        say("[中止] detached_policy=block_all：图片库未挂载时不允许跑任何更新。")
        write_report(out)
        return C.ST.EXIT_DETACHED

    # ---------- 1) 更新 sitemap / urls.json ----------
    step_time = {}      # 各步骤耗时（末尾那行「明细」用它，含 s01/s01b）
    if dry:
        say("[步骤] --dry：跳过 s01，直接用现有 urls.json 计算")
    else:
        rc, dt, lines = R.run_step("s01", "collect/s01_collect_urls.py", extra=yflag or [])
        for l in lines[-3:]:
            say("   " + l)
        say("[步骤] s01 rc=%d %.1fs" % (rc, dt))
        step_time["s01"] = dt
        if rc != 0:
            say("[中止] s01 失败，未改动任何数据")
            return 1

    # ---------- 1b) 分页列表页补漏（sitemap 之外的发现通道） ----------
    #   默认 --quick：只翻各归档第 1 页（新文章只会出现在第 1 页），~29 请求；
    #   --deep：穷举全部分页，命中 _rawdata/pages/ 缓存的页不再请求。
    extra = [] if deep else ["--quick"]
    rc, dt, lines = R.run_step("s01b", "collect/s01b_collect_pages.py", tail=8, extra=extra)
    for l in lines[-4:]:
        say("   " + l)
    say("[步骤] s01b %s rc=%d %.1fs" % ("deep" if deep else "quick", rc, dt))
    step_time["s01b"] = dt
    if rc != 0:
        # 发现通道之一挂掉不该阻断整个更新：s01 的 urls.json 仍然可用
        say("[提示] s01b 失败，本轮只用 sitemap 结果（跳过分页补漏）")

    urls = C.read_json(URLS, {}) or {}
    keys = parsed_keys()
    new = [u for u in urls if C.url_key(u) not in keys]
    pend_known = set(urls)
    _pend_p = C.pipeline_find(PAGES_PENDING_NAME)
    pend = [u for u in ((C.read_json(_pend_p, {}) or {}) if _pend_p else {}) if u not in pend_known]

    changed = []
    if refresh and keys:
        lm = parsed_lastmod(keys)
        for u, meta in urls.items():
            k = C.url_key(u)
            if k in lm and lm[k] and meta.get("lastmod") and lm[k] != meta["lastmod"]:
                changed.append(u)

    say("[增量] sitemap/urls.json 共 %d 篇 | 新增 %d 篇 | lastmod 变化 %d 篇"
        % (len(urls), len(new), len(changed)))
    if pend:
        say("[增量] 分页差集（未合并，--dry 只记录）%d 篇，见 %s"
            % (len(pend), _pend_p or "（无）"))
    for u in (new + changed)[:10]:
        say("        + %s" % u)
    if len(new) + len(changed) > 10:
        say("        ... 其余 %d 篇省略" % (len(new) + len(changed) - 10))

    if dry:
        say("[结论] --dry 结束：未抓取、未写入。去掉 --dry 即执行更新")
        write_report(out)
        return 0

    if not new and not changed and not force:
        say("[结论] 无新增/变化，跳过重建（--force 可强制重建站点）")
        write_report(out)
        return 0

    todo = new + changed
    if pages:
        todo = todo[:pages]
        say("[限制] --pages=%d：本次最多处理 %d 篇" % (pages, len(todo)))

    # ---------- 2) 本轮范围文件 ----------
    # 有新增就走范围模式：todo 落成 update_keys.json，各步骤用 --keys-file 接住。
    # --force 不写（= 全树档，顺便把上一轮可能被范围漏掉的半成品一并重收敛）。
    scope_path = None
    if todo and not force:
        scope_path = C.scope_write(todo, note="update.py 增量轮")
        say("[范围] 本轮只处理 %d 篇 → %s" % (len(todo), scope_path))
        say("       %s 走范围；s06 按触及年份局部重建；_verify 仍全树"
            % "/".join(R.SCOPE_STEPS))
    else:
        say("[范围] 全树模式（%s）：所有步骤逐篇扫整个库"
            % ("--force" if force else "本轮无新增，只建站"))

    # ---------- 3) --refresh：先备份再重抓 ----------
    moved = []
    if changed:
        items = []
        for u in changed:
            k = C.url_key(u)
            items.append(C.url_key(u))
        moved = move_to_backup(items)
        say("[刷新] 已将 %d 个年度容器移入 %s（抓取失败自动移回）" % (len(moved), BK))

    # ---------- 4) 跑流水线 ----------
    # 无新增且无变化时默认只重建站点；--force 跑完整流水线（含 s02/s03/s04i）：
    # 上一轮如果在 s04i 之前中断（图片还没下），新文章的图就靠这一档补齐。
    steps = STEPS if (new or changed or force) else [s for s in STEPS if s[0] in ("s04d", "s06", "verify")]
    if offline:
        dropped = [s[0] for s in steps if s[0] in IMAGE_STEPS]
        steps = [s for s in steps if s[0] not in IMAGE_STEPS]
        say("[图片库] 未挂载 -> 跳过 %s（不下载图片、不改 manifest）"
            % "/".join(dropped))
        if new:
            say("        本轮新增 %d 篇的正文图暂缺，页面按远程地址降级显示；"
                "插上移动硬盘后跑 `_scripts\\py.cmd _scripts\\build\\update.py --force` 自动补齐"
                "（s04i 只补 manifest/磁盘都缺的那些，不会重下旧图）" % len(new))
    fails = []
    skipped = []
    for name, script in steps:
        say("[步骤] >>> %s" % name)
        extra = R.step_extra(name, scope_path, yflag)
        rc, dt, lines = R.run_step(name, script, tail=4, extra=extra)
        for l in lines:
            say("   " + l)
        say("[步骤] <<< %s rc=%d %.1fs" % (name, rc, dt))
        step_time[name] = dt
        if C.ST.image_steps_skipped(rc):
            # 步骤内部的闸门拦下来了（拔盘/破损），按「跳过」记账，不算失败
            skipped.append(name)
            continue
        if rc != 0:
            fails.append((name, rc))
            break   # 失败即停，避免在半成品上建站

    # ---------- 5) 刷新失败回滚 ----------
    if moved:
        restored = 0
        for u in changed:
            k = C.url_key(u)
            _slot, cur = C.Y.locate(k)
            if not cur or not os.path.exists(cur):
                restored += rollback([m for m in moved if m[0].endswith(k + C.Y.MATERIAL_SUFFIX)])
        if restored:
            say("[刷新] 抓取失败的 %d 个容器已移回" % restored)

    # ---------- 6) 计数校验（只展示） ----------
    after = R.counts()
    say("-" * 66)

    def cell(v):
        return "未挂载" if v is None else v

    def delta(a, b):
        if a is None or b is None:
            return "-"
        return "%+d" % (a - b)

    rows = [
        ("sitemap/urls.json", after["urls"], after["urls"] - base["urls"]),
        ("年度容器(原料)", after["material"], after["material"] - base["material"]),
        ("  含解析产物", after["parsed"], after["parsed"] - base["parsed"]),
        ("  分页子页", after["subpages"], after["subpages"] - base["subpages"]),
        ("images 文件", cell(after["images"]), delta(after["images"], base["images"])),
        ("manifest 条目", after["manifest"], after["manifest"] - base["manifest"]),
        ("单篇页(各年目录)", after["articles"], after["articles"] - base["articles"]),
        ("年度 data.js", after["datajs"], delta(after["datajs"], base["datajs"])),
    ]
    for k, v, d in rows:
        say("[校验] %-18s %8s  (%s)" % (k, v, d))
    say("[校验] 0 字节图片 %s / 非 20 位命名 %s / 年份个数 %s / 图片库 %s"
        % (cell(after["img_zero"]), cell(after["img_badname"]), after["year_slots"],
           after.get("store_state")))
    if after.get("store_state") == "incremental":
        say("        （images 计数只统计本机目录，里面只有增量新图；历史图在别的电脑上，不计入）")
    if skipped:
        say("[校验] 因图片库闸门跳过的步骤：%s" % "/".join(skipped))

    diff = []
    if after["material"] != after["urls"]:
        diff.append("容器=%d vs urls=%d" % (after["material"], after["urls"]))
    if after["parsed"] != after["urls"]:
        diff.append("含解析=%d vs urls=%d" % (after["parsed"], after["urls"]))
    if after["articles"] != after["parsed"]:
        diff.append("单篇页=%d vs 含解析=%d" % (after["articles"], after["parsed"]))
    if after["datajs"] not in (None, after["parsed"]):
        diff.append("年度 data.js 条目=%s vs 含解析=%d" % (after["datajs"], after["parsed"]))
    if after["img_zero"] or after["img_badname"]:
        diff.append("图片异常：0 字节 %d / 非 20 位 %d" % (after["img_zero"], after["img_badname"]))

    if diff:
        say("[结论] 计数存在差异（仅提示，不影响已有页面）：" + "；".join(diff))
        say("        正常情形：sitemap 里有但站点 404 的文章、源站死链图。请自行决定是否处理。")
    else:
        note = ""
        if offline and new:
            note = "（图片库未挂载，正文图片尚未本地化）"
        elif after.get("store_state") == "incremental":
            note = "（新图已下载到本机 images/，历史图在别的电脑）"
        say("[结论] 计数一致：新增 %d 篇全部落地，已有页面未受影响%s"
            % (after["parsed"] - base["parsed"], note))

    say("[耗时] 总计 %.1fs  明细 %s"
        % (time.time() - t_all, " ".join("%s=%.0fs" % kv for kv in step_time.items())))
    write_report(out)
    if fails:
        say("UPDATE_FAIL " + " ".join("%s=%d" % f for f in fails))
        return 1
    say("UPDATE_OK %d" % int(time.time() - t_all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
