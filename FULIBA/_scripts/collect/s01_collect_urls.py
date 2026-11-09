# -*- coding: utf-8 -*-
"""
s01_collect_urls.py — 全量文章 URL 收集（sitemap 通道）
流程：sitemap.xml (index) → 逐个抓 sitemap-pt-post-YYYY-MM.xml → 汇总 URL + lastmod
幂等：已抓的 sitemap 片段不重抓（_rawdata/sitemaps/ 缓存）；url 清单**合并**去重。
注：结果与现有 urls.json 合并而非覆写 —— 分页通道（s01b）发现的条目不能被抹掉。

**年份范围（2026-10-31 年度化）**：默认只比对当年（按分片文件名里的 YYYY-MM），
往年月片根本不拉/不解析；新发现但发布年不在范围内 → 进 _pipeline/<日期>/urls_outofscope.json 备查，
不静默丢。逃生阀：`--years=all`（全量比对）或 `--years=2025,2026`。
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C


def is_sitemap_xml(t):
    """真的 sitemap 才是数据：必须是 XML 开头且有 <loc>。

    实测坑：代理没开、降级直连时 `/sitemap.xml` 会 **200 + 返回一个 HTML 页**（论坛的 sitemap 页面），
    旧代码看都不看就覆写 `_index.xml` 并当「0 条分片」正常走完，发现通道默默断掉。
    """
    head = (t or "")[:4000].lstrip()
    if not head.startswith("<?xml") and "<sitemapindex" not in head and "<urlset" not in head:
        return False
    return "<loc>" in (t or "")


def main():
    years = C.Y.parse_years_arg(
        next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--years=")), None))
    C.log("[s01] 比对范围（年）：%s" % ("全部槽位" if len(years) == len(C.Y.all_slots())
                                     else ",".join(years)))
    C.log("[s01] 抓取 sitemap index ...")
    pacer = C.Pacer(base=0.8, jitter=0.5, rest_every=30, rest_secs=2.0, name="sitemap")
    sess = C.make_session()

    r, txt = C.fetch(sess, C.BASE + "/sitemap.xml", pacer, ref=C.BASE + "/")
    if not r:
        C.log("[s01] 致命：sitemap index 获取失败")
        return 2

    idx_path = os.path.join(C.SITEMAP_DIR, "_index.xml")
    if is_sitemap_xml(txt):
        C.write_text(idx_path, txt)
        C.state_append("sitemap", "index", "DONE", count=len(txt))
        locs = re.findall(r"<loc>(.*?)</loc>", txt)
    else:
        # 拿回的不是 XML（代理未开时被顶回论坛页）：不能覆写缓存，按两级回退
        head = (txt or "")[:80].replace("\n", " ")
        cached = None
        if os.path.exists(idx_path):
            with open(idx_path, "r", encoding="utf-8", errors="replace") as f:
                cached = f.read()
        if cached and is_sitemap_xml(cached):
            C.log("[s01] 警告：/sitemap.xml 本次不是 XML（%s），沿用上次缓存的分片清单" % head)
            locs = re.findall(r"<loc>(.*?)</loc>", cached)
        else:
            names = sorted(n for n in os.listdir(C.SITEMAP_DIR)
                           if n.startswith("sitemap-pt-post-") and n.endswith(".xml"))
            if not names:
                C.log("[s01] 致命：/sitemap.xml 不是 XML（%s）且无可用缓存/本地月片。"
                      "常见原因：Clash 代理 127.0.0.1:7890 未开；本步不写盘，保留现有 urls.json" % head)
                print("S01_NOT_SITEMAP_XML")
                return 3
            C.log("[s01] 警告：/sitemap.xml 本次不是 XML（%s）；"
                  "改用本地已缓存的 %d 个月片作为清单来源（当年新增仍由 s01b 发现）"
                  % (head, len(names)))
            locs = [C.BASE + "/" + n for n in names]
    post_maps_all = [l for l in locs if "sitemap-pt-post-" in l]
    other = [l for l in locs if "sitemap-pt-post-" not in l]
    # 只比对范围内的年月片：往年月片不拉不解析（用户要求 2025 及以前即使有新增也不比对）
    post_maps, skipped_maps = [], []
    for sm in post_maps_all:
        m = re.search(r"sitemap-pt-post-(\d{4})", os.path.basename(sm))
        if m and m.group(1) not in years:
            skipped_maps.append(os.path.basename(sm))
        else:
            post_maps.append(sm)
    C.log("[s01] sitemap index: %d 个分片（比对 %d，往年跳过 %d，其他 %d）"
          % (len(locs), len(post_maps), len(skipped_maps), len(other)))
    if skipped_maps:
        C.log("[s01] 往年分片本轮不比对（共 %d 个，最早 %s 最新 %s）"
              % (len(skipped_maps), skipped_maps[0], skipped_maps[-1]))
    C.log("[s01] 非文章分片（跳过）: %s" % ", ".join(os.path.basename(u) for u in other))

    urls = {}   # url -> {lastmod, sitemap}
    failed = []
    for i, sm in enumerate(post_maps, 1):
        name = os.path.basename(sm)
        local = os.path.join(C.SITEMAP_DIR, name)
        if os.path.exists(local) and os.path.getsize(local) > 0:
            body = C.read_json(local + ".json")
            if body is None:
                with open(local, "r", encoding="utf-8", errors="replace") as f:
                    body = f.read()
        else:
            rr, tt = C.fetch(sess, sm, pacer, ref=C.BASE + "/")
            if not rr:
                C.log("[s01] %s 获取失败，保留已收集部分" % name)
                C.state_append("sitemap", name, "FAIL")
                failed.append(name)
                continue
            if not is_sitemap_xml(tt):
                C.log("[s01] %s 返回的不是 XML（%d 字节），不写缓存不计入" % (name, len(tt or "")))
                C.state_append("sitemap", name, "FAIL", reason="not_xml")
                failed.append(name)
                continue
            C.write_text(local, tt)
            body = tt
            C.state_append("sitemap", name, "DONE", bytes=len(tt))

        pairs = re.findall(r"<url>(.*?)</url>", body, re.S)
        n = 0
        for blk in pairs:
            u = re.search(r"<loc>(.*?)</loc>", blk, re.S)
            if not u:
                continue
            url = u.group(1).strip()
            lm = re.search(r"<lastmod>(.*?)</lastmod>", blk, re.S)
            urls[url] = {"lastmod": lm.group(1).strip() if lm else "",
                         "sitemap": name}
            n += 1
        if i % 10 == 0 or i == len(post_maps):
            C.log("[s01] 进度 %d/%d 分片，累计 URL %d" % (i, len(post_maps), len(urls)))

    out = os.path.join(C.SRC, "urls.json")
    # 与已有清单**合并**而不是整体覆盖：urls.json 是两套发现通道的并集，
    # sitemap（本步）之外还有分页列表页（s01b）；直接覆写会把「只在分页里
    # 出现过」的文章抹掉，parsed/html 还在但 urls 计数永远对不上。
    prev = C.read_json(out, {}) or {}
    merged = {}
    for u, meta in prev.items():
        m = dict(meta or {})
        m.setdefault("lastmod", "")
        merged[u] = m
    for u, meta in urls.items():
        m = merged.get(u)
        if m and m.get("source") == "pages":
            # 两通道都发现过：补上 sitemap 的 lastmod，保留发现来源可追溯
            m["lastmod"] = meta.get("lastmod", "") or m.get("lastmod", "")
            m["sitemap"] = meta.get("sitemap", "")
            m["also_in_sitemap"] = True
        else:
            merged[u] = meta
    # 新发现但发布年不在比对范围 → 不进 urls.json（否则会去抓往年），只登记备查
    # 备查档也按轮累积（读最近一份、写当天日期目录），跟 s01b 的 pending 台账同口径
    OUT_NAME = "urls_outofscope.json"
    _prev = C.pipeline_find(OUT_NAME)
    registry = (C.read_json(_prev, {}) or {}) if _prev else {}
    fresh, ignored = 0, 0
    for u, meta in urls.items():
        if u in prev:
            continue
        y = (meta.get("lastmod") or "")[:4]
        if y.isdigit() and y not in years:
            if u not in registry:
                registry[u] = {"lastmod": meta.get("lastmod", ""), "sitemap": meta.get("sitemap", ""),
                               "seen": time.strftime("%Y-%m-%d %H:%M:%S")}
                fresh += 1
            merged.pop(u, None)
            ignored += 1
    if registry:
        out_p = C.pipeline_rolling_path(OUT_NAME)
        C.write_json(out_p, registry)
        C.log("[s01] 往年新发现 %d 条未纳入比对（已登记，共 %d 条）→ %s"
              % (ignored, len(registry), out_p))
    n_sm = len(urls)
    urls = merged
    C.log("[s01] sitemap %d 条，与已有清单合并后 %d 条（分页补抓的不丢失）"
          % (n_sm, len(urls)))

    # 已确认失效的 URL 不进清单：sitemap 不会及时剔除已删除文章，
    # 否则每次重跑 s01 都会把它们合并回来，urls 数永远比可抓文章多（§8.1）。
    gone_p = os.path.join(C.SRC, "urls_gone.json")
    gone = set((it or {}).get("url") for it in (C.read_json(gone_p, []) or []))
    gone.discard(None)
    if gone:
        dropped = [u for u in list(urls) if u in gone]
        for u in dropped:
            urls.pop(u, None)
        if dropped:
            C.log("[s01] 剔除已确认失效 URL %d 条（urls_gone.json）" % len(dropped))
    C.write_json(out, urls)
    C.state_append("urls", "all", "DONE", count=len(urls), failed_sitemaps=len(failed),
                   kept_non_sitemap=len([m for m in urls.values() if m.get("source") == "pages"]))
    C.log("[s01] 完成：%d 篇文章 URL → %s（其中分页来源 %d 条）"
          % (len(urls), out, len([m for m in urls.values() if m.get("source") == "pages"])))
    if failed:
        C.log("[s01] 失败分片（重跑本脚本可续）: %s" % ", ".join(failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
