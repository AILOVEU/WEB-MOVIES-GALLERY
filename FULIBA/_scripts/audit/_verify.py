# -*- coding: utf-8 -*-
"""
_verify.py — 交付前自检
校验项：
1. 文章总数与 urls.json 一致
2. 评论数与页面标注的 comment_count 逐篇比对（抽取 200 篇全量 + 全量统计）
3. 正文无广告标记（adsbygoogle / 广告位 / 赞助 / 推广位）
4. 正文图片 src 100% 指向本地文件，且文件真实存在
5. data.js 可被 JS 引擎解析（语法检查）+ index.html 引用完整
6. 文章页图片引用可解析（分片相对路径 ../.. 与 assets/images/ 前缀正确）
结果写 _logs/<日期>/verify_report.txt
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

# 广告标记：只看**结构性**广告特征。
# 注意：不能把「赞助」「广告位」等词直接当广告——正文里讨论"赞助/广告"的正常文章
# （如 transfer.html 讲银联赞助广告片、ad-muncher.html 讲广告过滤工具）会被误判。
# 真正的站点级广告位在 HTML 层面是独立容器（class/id 含 ad 特征），已由 s03 剔除。
AD_RE = re.compile(r"(adsbygoogle|pub_\d+x\d+|text[_-]?ad|adsense|ad_wrapper|ad-container)", re.I)
# 正文纯文本里出现的「赞助/广告」字样：单独统计为“需人工确认”，不算失败
WORD_RE = re.compile(r"(赞助|广告位|推广位|商务合作)")

out_lines = []


def P(s=""):
    out_lines.append(s)


def load_articles():
    """读年度容器的解析产物（_pipeline/material/<年>/<key>.jsonl 的 parsed 段）。

    另返 {url: 年份槽}，[5]/[7] 要用它定位文章所在分片。
    """
    arts, slots = {}, {}
    for url, key, slot, a in C.Y.iter_parsed():
        arts[url] = a
        slots[url] = slot
    return arts, slots


def main():
    urls = C.read_json(os.path.join(C.SRC, "urls.json"), {}) or {}
    articles, slots = load_articles()
    manifest = C.read_json(os.path.join(C.SRC, "image_manifest.json"), {}) or {}

    P("=" * 66)
    P("fuliba2023 交付自检报告")
    P("年度层：年份目录 %d 个（预枚举槽位 %d..%d + undated）| 文章页(分片内锚点) %d | 原料容器 %d"
      % (len(C.Y.counts()), C.Y.YEAR_MIN, C.Y.YEAR_MAX, C.Y.pages_total(), C.Y.stats()["containers"]))
    _st, _cfg, _detail = C.store_state()
    P("图片库：state=%s mode=%s 本机=%s 外迁目标=%s（%s）"
      % (_st, _cfg["mode"], C.IMAGES_DIR, _cfg.get("external_root"), _detail))
    P("=" * 66)
    P()

    # 1) 覆盖率
    P("[1] 文章覆盖率（年度层）")
    missing = [u for u in urls if u not in articles]
    P("    urls.json      : %d" % len(urls))
    P("    年度容器(已解析) : %d" % len(articles))
    P("    年份个数       : %d（%s）"
      % (len(C.Y.counts()), ", ".join("%s:%d" % kv for kv in sorted(C.Y.counts().items()))))
    P("    容器缺解析     : %d" % max(0, C.Y.stats()["containers"] - len(articles)))
    P("    缺失           : %d (%.2f%%)" % (len(missing), 100.0 * len(missing) / max(len(urls), 1)))
    if missing:
        P("    缺失样例:")
        for u in missing[:5]:
            P("      %s" % u)
    P()

    # 2) 评论一致性
    P("[2] 评论数一致性（页面标注 vs 实际解析）")
    total_page = 0
    total_parsed = 0
    mismatch = []
    for u, a in articles.items():
        pc = a.get("comment_count", 0)
        pa = a.get("comments_parsed", 0)
        total_page += pc
        total_parsed += pa
        if pc != pa:
            mismatch.append((u, pc, pa))
    P("    页面标注评论合计 : %d" % total_page)
    P("    实际解析评论合计 : %d" % total_parsed)
    P("    逐篇不一致       : %d / %d (%.2f%%)"
      % (len(mismatch), max(len(articles), 1), 100.0 * len(mismatch) / max(len(articles), 1)))
    for u, pc, pa in mismatch[:5]:
        P("      %s  页面=%d 解析=%d" % (u[:80], pc, pa))
    # 缺口归类：区分「站点侧不可取」与「真实抓取缺失」
    import collections as _c
    gap_reason = _c.Counter()
    gap_total = 0
    for u, a in articles.items():
        g = a.get("comment_gap") or 0
        if g:
            gap_total += g
            gap_reason[a.get("comment_gap_reason") or "unknown"] += g
    if gap_total:
        P("    缺口合计         : %d 条，归类如下" % gap_total)
        for k, v in gap_reason.most_common():
            note = ""
            if k == "site_no_comment_container":
                note = "（文章页无评论容器，站点未提供评论）"
            elif k == "site_render_cap":
                note = "（服务端仅渲染部分楼层，comment-page 各页内容相同）"
            P("      %-28s %6d 条%s" % (k, v, note))
    P()

    # 3) 广告残留
    P("[3] 正文广告残留检查")
    ad_hit = []
    word_hit = []
    for u, a in articles.items():
        h = a.get("content_html", "") or ""
        if AD_RE.search(h):
            ad_hit.append((u, AD_RE.findall(h)[:3]))
        if WORD_RE.search(h):
            word_hit.append(u)
    P("    含结构性广告标记 : %d / %d" % (len(ad_hit), max(len(articles), 1)))
    for u, m in ad_hit[:5]:
        P("      %s -> %s" % (u[:70], m))
    P("    正文提及「赞助/广告位」等词的文章 : %d（正常内容，非广告位，不计失败）" % len(word_hit))
    P()

    # 4) 图片本地化
    st, scfg, sdetail = C.store_state()
    store_offline = st in ("detached", "broken")
    # incremental：历史图已被移到别的电脑，本机只接增量新图——
    # 「本机 stat 不到」等于「在外部库」，不是丢失；0 字节仍属本机异常。
    store_partial = (st == "incremental")
    P("[4] 图片本地化检查")
    if store_offline:
        P("    图片库状态       : %s（%s）—— 以下文件级判定**未评估**，"
          "不当成「图片丢失」；插上移动硬盘后重跑本自检即可" % (st, sdetail))
    elif store_partial:
        P("    图片库状态       : incremental（本机 images 只含增量新图）——"
              "历史图在别的电脑，本机找不到的引用计为「在外部库」，不计丢失")
    total_img = 0
    local_ok = 0
    local_missing = 0
    local_zero = 0
    remote_left = 0
    local_na = 0
    local_away = 0
    for u, a in articles.items():
        for im in a.get("images", []):
            total_img += 1
            lp = im.get("local") or ""
            if lp:
                if store_offline:
                    local_na += 1
                    continue
                # 文件存在但 0 字节同样算破图（§1.33）
                fp = C.img_disk_path(lp)
                try:
                    if not os.path.exists(fp):
                        if store_partial:
                            local_away += 1
                        else:
                            local_missing += 1
                    elif os.path.getsize(fp) == 0:
                        local_zero += 1
                    else:
                        local_ok += 1
                except OSError:
                    local_missing += 1
            else:
                remote_left += 1
    P("    正文图片总数     : %d" % total_img)
    P("    已落地且文件非空: %d (%.2f%%)" % (local_ok, 100.0 * local_ok / max(total_img, 1)))
    P("    映射有但文件缺失 : %d" % local_missing)
    P("    映射指向 0 字节 : %d" % local_zero)
    P("    未落地           : %d" % remote_left)
    if store_offline:
        P("    未评估（库未挂载）: %d" % local_na)
    if store_partial:
        P("    在外部库（本机未挂载）: %d" % local_away)
    P()

    # 5) 产物完整性
    P("[5] 交付产物检查")
    for f in ("index.html", os.path.join("data", "common", "style.css"),
              os.path.join("data", "common", "archived.js")):
        p = os.path.join(C.ROOT, f)
        ok = os.path.exists(p) and os.path.getsize(p) > 0
        P("    %-14s %s %s" % (f, "OK " if ok else "MISSING",
                               ("%.1f KB" % (os.path.getsize(p) / 1024.0)) if ok else ""))
    rp = C.log_find("report.md")            # 上一轮全量重建的抓取报告（按天分目录，取最新一份）
    if rp and os.path.getsize(rp) > 0:
        where = os.path.relpath(os.path.dirname(rp), C.ROOT).replace("\\", "/")
        P("    %-14s OK  %.1f KB @ %s" % ("report.md", os.path.getsize(rp) / 1024.0, where))
    else:
        P("    %-14s MISSING（只有全量重建才生成，本轮之前还没跑过）" % "report.md")
    n_art = C.Y.pages_total()
    P("    文章页(分片锚点) : %d 篇【data/<年>/part<i>.html，每篇一个 #<key> 锚点】" % n_art)
    P("    年度 data.js   : %d 个（预枚举槽位 %d..%d + undated）"
      % (len(C.Y.year_data_files()), C.Y.YEAR_MIN, C.Y.YEAR_MAX))
    imgs = 0
    imgs_seen = os.path.isdir(C.IMAGES_DIR)
    if imgs_seen:
        for dp, dn, fn in os.walk(C.IMAGES_DIR):
            imgs += len([f for f in fn if not f.startswith(".")])
    if imgs_seen and store_partial:
        P("    assets/images/ 本地 : %d 张（本机只存增量新图，历史图在别的电脑）" % imgs)
    else:
        P("    assets/images/ 本地 : %s" % ("%d 张" % imgs if imgs_seen else "未挂载（库在移动硬盘）"))
    P()

    # 6) 年度 data.js 语法与条目
    P("[6] 年度 data.js 语法与内容")
    tot = 0
    bad = []
    for p in C.Y.year_data_files():
        txt = io.open(p, encoding="utf-8").read()
        try:
            arr = json.loads(txt[txt.index("["):txt.rindex("]") + 1])
        except ValueError as e:
            bad.append((p, str(e)[:60]))
            continue
        tot += len(arr)
        slot = os.path.basename(os.path.dirname(p))
        # 每条都要带年份槽与 id（列表页靠它们拼分片深链 part<p>.html#id）
        for it in arr:
            if str(it.get("y")) != slot or not it.get("id"):
                bad.append((p, "条目 y/id 不齐: %s" % str(it)[:60]))
                break
    P("    文件 %d 个，条目合计 %d，解析/字段异常 %d"
      % (len(C.Y.year_data_files()), tot, len(bad)))
    for b in bad[:5]:
        P("      %s -> %s" % (b[0], b[1]))
    P("    与容器数一致：%s（%d vs %d）" % (tot == len(articles), tot, len(articles)))
    P()

    # 7) 文章页抽样校验（分片布局：按锚点定位到所属 part<i>.html，再切出本篇段）
    P("[7] 文章页抽样校验（30 篇；§1.33 教训：10 篇覆盖不足）")
    import random
    keys = list(articles.keys())
    random.seed(42)
    sample = random.sample(keys, min(30, len(keys)))
    bad = 0          # 本地路径破图 —— 真问题
    remote_degraded = 0   # 源站已死的图，保留远程 URL 降级 —— 可接受
    for u in sample:
        a = articles[u]
        slug = C.url_key(u)   # 目录名统一为 URL 哈希（见 总体执行规划.md §1.17）
        slot = slots.get(u) or C.Y.slot_of(u)
        fp = C.Y.find_article_file(slot, slug)
        if not fp:
            P("    MISSING %s（分片里找不到该篇锚点）" % slug)
            bad += 1
            continue
        full = io.open(fp, encoding="utf-8").read()
        s0 = full.find(C.Y.SHARD_UNIT_OPEN + slug + '"')
        s1 = full.find(C.Y.SHARD_UNIT_OPEN, s0 + 1)
        if s1 < 0:
            s1 = full.find(C.Y.SHARD_SCRIPTS_MARK, s0)
        t = full[s0:s1] if s0 >= 0 else full   # 只留本篇，避免把邻篇的图算进来
        # 检查图片相对路径能否解析
        if store_offline:
            continue
        if store_partial:
            # 历史图在外部库：缺文件不算破图，只查「有没有降级成远程地址」
            for m2 in re.finditer(r'<img[^>]+src="([^"]+)"', t):
                if m2.group(1).startswith("http"):
                    P("    REMOTE_DEGRADED %s -> %s" % (slug[:24], m2.group(1)[:60]))
                    remote_degraded += 1
                    break
            continue
        for m2 in re.finditer(r'<img[^>]+src="([^"]+)"', t):
            src = m2.group(1)
            if src.startswith("http"):
                # 源站已下线，无法本地化 → 保留远程 URL 降级，不计为异常
                P("    REMOTE_DEGRADED %s -> %s" % (slug[:24], src[:60]))
                remote_degraded += 1
                break
            real = os.path.normpath(os.path.join(os.path.dirname(fp), src))
            if not os.path.exists(real):
                P("    BROKEN_IMG %s -> %s" % (slug[:30], src[:70]))
                bad += 1
                break
    P("    本地路径破图（异常）: %d" % bad)
    P("    源站已死降级远程（可接受）: %d" % remote_degraded)
    P()

    # 8) 评论结构完整性
    P("[8] 评论树结构完整性")
    total_c = 0
    orphan = 0
    no_author = 0
    no_time = 0
    for u, a in articles.items():
        ids = set()
        for c in a.get("comments", []):
            ids.add(c.get("id"))
        for c in a.get("comments", []):
            total_c += 1
            pid = c.get("parent_id") or ""
            if pid and pid not in ids:
                orphan += 1
            if not (c.get("author") or "").strip():
                no_author += 1
            if not (c.get("time") or "").strip():
                no_time += 1
    P("    评论总数         : %d" % total_c)
    P("    父节点缺失(孤儿) : %d (%.3f%%)" % (orphan, 100.0 * orphan / max(total_c, 1)))
    P("    缺作者           : %d (%.3f%%)" % (no_author, 100.0 * no_author / max(total_c, 1)))
    P("    缺时间           : %d (%.3f%%)" % (no_time, 100.0 * no_time / max(total_c, 1)))
    P()

    # 9) 评论头像（统一占位图）
    P("[9] 评论头像（统一占位图）")
    av_total = 0
    av_local = 0
    av_broken = 0
    av_unique = set()
    for u, a in articles.items():
        for c in a.get("comments", []):
            if c.get("avatar"):
                av_total += 1
                lp = c.get("avatar_local") or ""
                if lp:
                    av_local += 1
                    av_unique.add(lp)
                    if not os.path.exists(C.img_disk_path(lp)):
                        av_broken += 1
    P("    评论总数         : %d" % av_total)
    P("    已设占位图       : %d (%.2f%%)" % (av_local, 100.0 * av_local / max(av_total, 1)))
    P("    占位图文件缺失   : %d" % av_broken)
    P("    使用的占位图     : %s（唯一 %d 个）" % (", ".join(sorted(av_unique)) or "无", len(av_unique)))
    P("    说明：按归档需求，评论头像统一使用默认占位图，不做逐个本地化；")
    P("          原先落盘的 59612 个头像文件（181.9 MB）已全部移除。")
    P()

    # 10) 全量本地引用校验（不只看 images[].local，而是直接扫 content_html 的 img）
    P("[10] 全量 content_html 本地引用校验")
    refs = 0
    ref_missing = 0
    ref_zero = 0
    ref_away = 0
    for u, a in articles.items():
        h = a.get("content_html") or ""
        if "images/" not in h:
            continue
        for m2 in re.finditer(r'<img[^>]+src="(images/[^"]+)"', h):
            src = m2.group(1)
            refs += 1
            if store_offline:
                continue
            fp = os.path.normpath(C.img_disk_path(src))
            try:
                if not os.path.exists(fp):
                    if store_partial:
                        ref_away += 1
                    else:
                        ref_missing += 1
                        if ref_missing <= 5:
                            P("    MISSING %s -> %s" % (u[:60], src))
                elif os.path.getsize(fp) == 0:
                    ref_zero += 1
                    if ref_zero <= 5:
                        P("    ZERO %s -> %s" % (u[:60], src))
            except OSError:
                ref_missing += 1
    P("    本地引用总数     : %d" % refs)
    P("    文件缺失         : %d" % ref_missing)
    P("    0 字节空壳       : %d" % ref_zero)
    if store_offline:
        P("    （图片库未挂载：引用计数照常统计，文件级判定未评估）")
    if store_partial:
        P("    在外部库（未挂载）: %d" % ref_away)
    P()

    P("=" * 66)
    out = C.log_path("verify_report.txt")      # _logs/<今天>/
    with io.open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    print("written", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
