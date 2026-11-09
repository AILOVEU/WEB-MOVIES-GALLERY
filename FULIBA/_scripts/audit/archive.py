# -*- coding: utf-8 -*-
"""
archive.py — 归档名单执行器（配合站内「归档」按钮的标记名单）

完整流程：
  1) 站内标记：index 卡片「归档」= 待移入；archived.html 名录「移出归档」= 待移出
     （标记存浏览器 localStorage，顶部「归档」按钮带待办角标）
  2) 导出：archived.html「导出名单」→ 下载 archive_ops.json
  3) 合并：_scripts\\py.cmd _scripts\\audit\\archive.py --import <archive_ops.json>   → 合并进 _pipeline/archive_ops.json
  4) 执行：_scripts\\py.cmd _scripts\\audit\\archive.py --apply
     - move_in  移入归档：删专属图片（与保留文章共用比对）+ html/parsed/子页/单篇页，
       urls.json 移除、urls_gone 登记（防增量更新抓回）、skipped_promo 台账登记
     - move_out 移出归档：urls_gone/台账移除、url 写回 urls.json，
       增量跑 s02→s03→s04i→s04l→s04f→s04d→s06→verify 重抓；抓取失败自动回滚登记
  5) 回到 archived.html 点「清空名单」清掉已执行标记

用法:
  _scripts\\py.cmd _scripts\\audit\\archive.py                     # 查看当前名单
  _scripts\\py.cmd _scripts\\audit\\archive.py --import <file>     # 合并浏览器导出的名单（可多次，增量合并）
  _scripts\\py.cmd _scripts\\audit\\archive.py --apply             # 执行移入/移出
  _scripts\\py.cmd _scripts\\audit\\archive.py --clear             # 清空名单文件（慎用）

设计要点：
1. move_in 先全量校验（parsed 存在、url 在 urls.json），任何一条不合格即跳过该条，
   绝不部分删除某篇文章的资源（与 T0-T2 清理同口径：先校验后动文件）。
2. 图片删除前与全部保留文章的 parsed 比对 URL，共用的不删；manifest 同步清理。
3. move_out 复用流水线幂等性：只把 url 写回 urls.json，s02/s03/s04i 天然跳过已完成项；
   若 s02 抓不到（原站 404 等），该条回滚（恢复台账+urls_gone），不影响其他条。
"""
import os
import re
import sys
import json
import glob
import shutil
import datetime

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

OPS = os.path.join(C.SRC, "archive_ops.json")
LEDGER = os.path.join(C.SRC, "skipped_promo.json")
URLS = os.path.join(C.SRC, "urls.json")
GONE = os.path.join(C.SRC, "urls_gone.json")
MAN = os.path.join(C.SRC, "image_manifest.json")
TODAY = datetime.date.today().isoformat()


def load_ops():
    return C.read_json(OPS, {"move_in": [], "move_out": []}) or {"move_in": [], "move_out": []}


def save_ops(ops):
    C.write_json(OPS, ops)


def norm_entry(e):
    """补全条目字段；key 缺失时从 url 推导。返回 None 表示无效。"""
    if not isinstance(e, dict) or not e.get("url"):
        return None
    e = dict(e)
    if not e.get("key"):
        e["key"] = C.url_key(e["url"])
    return e


def dedup(ops):
    """同 url 同时出现在两侧 → 丢弃后到的一条（ts 大者保留）。"""
    by_url = {}
    for side in ("move_in", "move_out"):
        for e in ops[side]:
            u = e.get("url", "")
            ts = e.get("ts", 0)
            if u in by_url:
                old_side, old_ts = by_url[u]
                if ts >= old_ts:
                    ops[old_side] = [x for x in ops[old_side] if x.get("url") != u]
                    by_url[u] = (side, ts)
                else:
                    ops[side] = [x for x in ops[side] if x is not e]
            else:
                by_url[u] = (side, ts)
    return ops


def cmd_import(path):
    if not os.path.exists(path):
        C.log("[archive] 名单文件不存在：%s" % path)
        return 2
    d = C.read_json(path, None)
    if not isinstance(d, dict):
        C.log("[archive] 名单文件格式不对（需 JSON 对象含 move_in/move_out）")
        return 2
    ops = load_ops()
    n_in = n_out = 0
    for e in d.get("move_in", []) or []:
        e = norm_entry(e)
        if e:
            ops["move_in"].append(e)
            n_in += 1
    for e in d.get("move_out", []) or []:
        e = norm_entry(e)
        if e:
            ops["move_out"].append(e)
            n_out += 1
    ops = dedup(ops)
    save_ops(ops)
    C.log("[archive] 合并完成：+待移入 %d，+待移出 %d；当前待移入 %d / 待移出 %d"
          % (n_in, n_out, len(ops["move_in"]), len(ops["move_out"])))
    return 0


def collect_img_urls(a):
    out = []
    for im in a.get("images", []) or []:
        if im.get("url"):
            out.append(im["url"])
    for pg in a.get("pages", []) or []:
        for im in pg.get("images", []) or []:
            if im.get("url"):
                out.append(im["url"])
    return out


def parsed_path(key):
    """年度容器路径（_pipeline/material/<年>/<key>.jsonl）；找不到返回 undated 下的预期路径。"""
    _slot, p = C.Y.locate(key)
    return p or C.Y.material_path(C.Y.UNDATED, key)


def apply_move_in(entries, urls, gone, man, ledger):
    """移入归档：校验 → 共用比对 → 删资源 → 改台账。返回 (ok_entries, skipped, freed_bytes, n_files)。"""
    ok, skip = [], []
    # ---- 全量校验，任何一条不合格整条跳过 ----
    arts = {}
    for e in entries:
        slot, cpath = C.Y.locate(e["key"])
        a = C.Y.read_parsed(key=e["key"])
        if not a or not a.get("url"):
            skip.append((e, "年度容器里没有解析产物（可能已删除或 key 不符）"))
            continue
        if e["url"] not in urls:
            skip.append((e, "url 不在 urls.json（可能已删除）"))
            continue
        arts[e["key"]] = (e, a, cpath, slot)
        ok.append(e)

    # ---- 共用比对：保留文章引用的图片 URL 集合 ----
    del_keys = set(arts.keys())
    referenced = set()
    for _u, key, _slot, a in C.Y.iter_parsed():
        if key in del_keys:
            continue
        referenced.update(collect_img_urls(a))

    freed = 0
    n_files = 0
    for e in ok:
        a = arts[e["key"]][1]
        slot = arts[e["key"]][3]
        # 1) 专属图片
        local_done = set()
        for u in collect_img_urls(a):
            if u in referenced:
                continue
            info = man.get(u)
            if not info or not info.get("local"):
                continue
            lp = info["local"]
            if lp in local_done:
                del man[u]
                continue
            local_done.add(lp)
            fp = C.img_disk_path(lp)
            if os.path.exists(fp):
                try:
                    freed += os.path.getsize(fp)
                    os.remove(fp)
                    n_files += 1
                except OSError as ex:
                    C.log("[archive] 警告：图片删除失败 %s（%s）" % (lp, ex))
            del man[u]
        # 2) 数据容器：一篇一容器（原始 HTML + 解析产物 + 子页）
        for fp in [arts[e["key"]][2]]:
            if fp and os.path.exists(fp):
                try:
                    freed += os.path.getsize(fp)
                    os.remove(fp)
                except OSError as ex:
                    C.log("[archive] 警告：容器删除失败 %s（%s）" % (fp, ex))
        C.Y.forget_index()
        # 3) 页面：分片布局下该篇是某个 part<i>.html 里的一个锚点，从分片中摘出即可
        #    （分片为多篇共享，不按整片计 freed 字节；下次全量重建会重排收拢空洞）
        if slot:
            hit, _pp = C.Y.remove_article_from_parts(slot, e["key"])
            if not hit:
                C.log("[archive] 提示：分片中未找到该篇锚点（可能已被摘除）：%s/%s"
                      % (slot, e["key"]))
        # 3) 台账三件套
        urls.pop(e["url"], None)
        gone.append({"url": e["url"], "status": 0, "reason": "archive_user_confirmed",
                     "checked": TODAY,
                     "note": "归档名单移入（archive.py）：%s" % e.get("title", "")})
        ledger.append({"key": e["key"], "url": e["url"], "title": e.get("title", ""),
                       "date": e.get("date", ""), "category": e.get("category", ""),
                       "tier": "manual", "deleted": TODAY,
                       "note": "归档名单移入"})
        C.log("[archive] 已移入归档：%s | %s" % (e.get("date", ""), e.get("title", "")[:40]))
    return ok, skip, freed, n_files


def apply_move_out(entries, urls, gone, ledger):
    """移出归档：恢复 urls 登记 → 重抓流水线 → 失败回滚。返回 (ok, skip, rollback)。"""
    ok, skip, rollback = [], [], []
    for e in entries:
        idx = next((i for i, r in enumerate(ledger)
                    if r.get("key") == e["key"] or r.get("url") == e["url"]), None)
        if idx is None:
            skip.append((e, "不在已归档台账 skipped_promo.json 中"))
            continue
        r = ledger[idx]
        e.setdefault("title", r.get("title", ""))
        e.setdefault("date", r.get("date", ""))
        e.setdefault("category", r.get("category", ""))
        e["_ledger_idx"] = idx
        ok.append(e)

    if not ok:
        return ok, skip, rollback

    # 恢复登记（先记下回滚材料，抓取失败再恢复）
    for e in ok:
        urls[e["url"]] = {"lastmod": "", "sitemap": "archive_restore"}
        gone[:] = [g for g in gone if g.get("url") != e["url"]]
        ledger.pop(e["_ledger_idx"])

    # 重抓：s02 只补缺，天然增量（rebuild 在 _scripts/build/，单独挂上路径）
    sys.path.insert(0, os.path.join(C.SCRIPT_DIR, "build"))
    import rebuild as R
    # 只重抓这几篇 → 先写本轮范围文件。以前这段是整条流水线全树跑（移出 1 篇也要
    # 十分钟，因为 s03/s04d/s06 会把七千篇全重做），现在跟 update.py 同一套口径：
    # s02/s02b/s03/s04i/s04f/s04d 只处理这批，s06 只重建触及的年份，verify 仍全树。
    scope_path = C.scope_write([e["url"] for e in ok], note="archive.py 移出归档重抓")
    C.log("[archive] 本轮范围 %d 篇 → %s" % (len(ok), scope_path))
    rc, dt, tail = R.run_step("s02", "collect/s02_fetch_html.py",
                              extra=R.step_extra("s02", scope_path))
    if rc != 0:
        C.log("[archive] s02 退出码 %d（详见 %s）" % (rc, C.log_path("rebuild_s02.log")))

    # 抓取失败的回滚
    still = []
    for e in ok:
        _slot, cpath = C.Y.locate(e["key"])
        if cpath and os.path.exists(cpath) and C.Y.has_raw(key=e["key"]):
            still.append(e)
        else:
            rollback.append((e, "HTML 重抓失败（原站不可达或已 404）"))
            urls.pop(e["url"], None)
            ledger.append({"key": e["key"], "url": e["url"], "title": e.get("title", ""),
                           "date": e.get("date", ""), "category": e.get("category", ""),
                           "tier": "manual", "deleted": TODAY,
                           "note": "归档移出重抓失败，自动回滚登记"})
            gone.append({"url": e["url"], "status": 0, "reason": "archive_user_confirmed",
                         "checked": TODAY,
                         "note": "归档名单移出重抓失败回滚：%s" % e.get("title", "")})
    ok = still
    if not ok:
        return ok, skip, rollback

    for step, script in (("s03", "parse/s03_parse.py"), ("s04i", "images/s04i_fetch_body_fast.py"),
                         ("s04l", "images/s04l_proxy_unwrap.py"),
                         ("s04f", "images/s04f_rebuild_manifest.py"),
                         ("s04d", "images/s04d_rewrite.py"),
                         ("s06", "build/s06_build_site.py"),
                         ("verify", "audit/_verify.py")):
        rc, dt, tail = R.run_step(step, script, extra=R.step_extra(step, scope_path))
        C.log("[archive] %-6s rc=%d %.0fs" % (step, rc, dt))

    # 解析失败的也回滚
    final = []
    for e in ok:
        if os.path.exists(parsed_path(e["key"])):
            final.append(e)
            C.log("[archive] 已移出归档（重抓完成）：%s | %s" % (e.get("date", ""), e.get("title", "")[:40]))
        else:
            rollback.append((e, "重抓后 parsed 仍缺失"))
            urls.pop(e["url"], None)
            ledger.append({"key": e["key"], "url": e["url"], "title": e.get("title", ""),
                           "date": e.get("date", ""), "category": e.get("category", ""),
                           "tier": "manual", "deleted": TODAY, "note": "归档移出重抓失败，自动回滚登记"})
            gone.append({"url": e["url"], "status": 0, "reason": "archive_user_confirmed",
                         "checked": TODAY,
                         "note": "归档名单移出重抓失败回滚：%s" % e.get("title", "")})
    return final, skip, rollback


def cmd_apply():
    ops = dedup(load_ops())
    ins = [e for e in (norm_entry(x) for x in ops["move_in"]) if e]
    outs = [e for e in (norm_entry(x) for x in ops["move_out"]) if e]
    if not ins and not outs:
        C.log("[archive] 名单为空（先在站内标记并 --import）")
        return 0
    C.log("[archive] 待移入 %d 篇，待移出 %d 篇" % (len(ins), len(outs)))

    urls = C.read_json(URLS, {}) or {}
    gone = C.read_json(GONE, []) or []
    man = C.read_json(MAN, {}) or {}
    ledger = C.read_json(LEDGER, []) or []
    if man is None:
        man = {}

    applied_in = applied_out = 0
    freed = n_files = 0
    if ins:
        ok, skip, freed, n_files = apply_move_in(ins, urls, gone, man, ledger)
        applied_in = len(ok)
        for e, why in skip:
            C.log("[archive] 跳过移入：%s | %s" % (e.get("title", "")[:36], why))
    if outs:
        ok, skip, rollback = apply_move_out(outs, urls, gone, ledger)
        applied_out = len(ok)
        for e, why in skip + rollback:
            C.log("[archive] 跳过/回滚移出：%s | %s" % (e.get("title", "")[:36], why))

    # 落盘（仅在确实动过时写）
    if applied_in:
        C.write_json(URLS, urls)
        C.write_json(GONE, gone)
        C.write_json(MAN, man)
    if applied_in or applied_out:
        C.write_json(LEDGER, ledger)
    C.write_json(OPS, {"move_in": [], "move_out": []})

    C.log("[archive] ===== 执行完毕 =====")
    C.log("[archive] 移入归档 %d 篇，释放 %.2f MB（图片 %d 文件）" % (applied_in, freed / 1048576.0, n_files))
    C.log("[archive] 移出归档（重抓）%d 篇" % applied_out)
    C.log("[archive] 名单文件已清空；浏览器端请点「清空名单」同步清除标记")
    return 0


def cmd_clear():
    save_ops({"move_in": [], "move_out": []})
    C.log("[archive] 名单文件已清空")
    return 0


def cmd_list():
    ops = load_ops()
    C.log("[archive] 待移入 %d 篇：" % len(ops["move_in"]))
    for e in ops["move_in"]:
        C.log("  [in] %s | %s" % (e.get("date", "?"), (e.get("title") or e.get("url", ""))[:60]))
    C.log("[archive] 待移出 %d 篇：" % len(ops["move_out"]))
    for e in ops["move_out"]:
        C.log("  [out] %s | %s" % (e.get("date", "?"), (e.get("title") or e.get("url", ""))[:60]))
    if not ops["move_in"] and not ops["move_out"]:
        C.log("（空）站内标记后导出，再 --import 合并")
    return 0


def main():
    args = sys.argv[1:]
    if not args:
        return cmd_list()
    if args[0] == "--import":
        if len(args) < 2:
            C.log("[archive] 用法：--import <archive_ops.json>")
            return 2
        return cmd_import(args[1])
    if args[0] == "--apply":
        return cmd_apply()
    if args[0] == "--clear":
        return cmd_clear()
    C.log("[archive] 未知参数：%s（支持 --list 默认 / --import <file> / --apply / --clear）" % args[0])
    return 2


if __name__ == "__main__":
    sys.exit(main())
