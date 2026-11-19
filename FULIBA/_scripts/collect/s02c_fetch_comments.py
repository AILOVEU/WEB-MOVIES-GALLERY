# -*- coding: utf-8 -*-
"""
s02c_fetch_comments.py — 评论增量抓取（每次 update 都跑，只增不删）

背景（踩过的坑，勿删）:
- 评论存在年度容器 parsed 段的 `comments` 数组，唯一键是 `comments[].id`（WordPress comment id）。
- 当前 s03 只解析文章首页的评论（约 30~75 条）；历史 parsed 里多出来的评论只存在于 parsed，
  不在 raw 里。**绝不能 drop_parsed / 整段覆盖 comments**，否则会静默丢掉 3 万条历史评论。
- 评论翻页是 query 参数 `?cpage=N&commentsort=hot|asc|desc#comments`，不是 nextpage 子页。
  三条排序链返回的 id 集合不同，须三链各自翻页后按 id 求并集（实测单链会漏）。

本步骤做什么:
- 选帖：默认只处理「最近 --days 天（默认 60）发布」的文章；--all 忽略日期；
  --keys-file=<路径>（update.py 的范围文件）时**额外**纳入本轮新增文章。
- 抓评论：对每篇按 hot/asc/desc 三链 + 各自 cpage 翻页抓全，解析出评论节点。
- 合并：以 id 为键，旧评论原样保留（内容/字段一个不动），只把新 id 的评论追加进去。
  页面标注数与实际可抓数的缺口（站点渲染上限）只记台账，不臆造评论。
- 写回：走 C.Y.write_parsed()（自动重算 meta.n_comments、刷新 stamp.parse、内容相同不写盘）。
- 新评论补 `avatar_local`/`avatar_is_default` 占位字段，兼容 s04d 回写与 s06 渲染。

参数:
  --days=N      只处理最近 N 天发布的文章（默认 60）；0 表示不限日期
  --all         等同 --days=0（全库评论，慢，仅手动补漏用）
  --keys-file=  本轮范围（update.py 写的 update_keys.json）：范围内的文章无条件纳入
  --dry         只统计将新增多少评论，不抓详情、不写盘
"""
import os
import re
import sys
import time
from datetime import date, timedelta

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

from lxml import html as LH

# 复用 s03 的评论解析与文本清洗函数（import 无副作用：s03 的 main 在 __main__ 保护下）
_PARSE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parse")
if _PARSE_DIR not in sys.path:
    sys.path.insert(0, _PARSE_DIR)
import s03_parse as S03

SORTS = ("hot", "asc", "desc")
AVATAR_PLACEHOLDER = "assets/avatar-placeholder.png"


def parse_args():
    days = 60
    keys_path = None
    dry = False
    for a in sys.argv[1:]:
        if a.startswith("--days="):
            days = int(a.split("=", 1)[1])
        elif a == "--all":
            days = 0
        elif a.startswith("--keys-file="):
            keys_path = a.split("=", 1)[1]
        elif a == "--dry":
            dry = True
    return days, keys_path, dry


def comment_ids_and_pages(txt):
    """返回 (该页评论 id 集合, 该页给出的最大 cpage)。"""
    tree = LH.fromstring(txt)
    ids = set(x.replace("comment-", "").strip()
              for x in tree.xpath("//li[contains(@class,'comment')]/@id")
              if x.startswith("comment-"))
    cps = []
    for h in tree.xpath("//div[contains(@class,'comt-pagination')]//a/@href"):
        m = re.search(r"cpage=(\d+)", h)
        if m:
            cps.append(int(m.group(1)))
    return ids, (max(cps) if cps else 1)


def page_count(txt):
    """页面标注的评论数（"评论 (N)" / "评论 N"）。取不到返回 None。"""
    tree = LH.fromstring(txt)
    raw = " ".join(tree.xpath("//div[@id='comments']//h3//text()"))
    m = re.search(r"(\d+)", raw)
    return int(m.group(1)) if m else None


def fetch_all_comments(sess, pacer, url):
    """三链×翻页抓全，返回 (id->评论节点 dict, 页面标注数)。

    解析用 s03.extract_comments，保证字段与历史 parsed 完全一致。
    """
    found = {}
    declared = None
    for sort in SORTS:
        first = url + "?commentsort=%s#comments" % sort
        r, txt = C.fetch(sess, first, pacer, tries=4, ref=C.BASE + "/", timeout=45)
        if txt is None:
            continue
        if declared is None:
            declared = page_count(txt)
        ids, maxcp = comment_ids_and_pages(txt)
        # 解析本页全部评论节点
        for node in S03.extract_comments(LH.fromstring(txt)):
            if node.get("id") and node["id"] not in found:
                found[node["id"]] = node
        for cp in range(2, maxcp + 1):
            purl = url + "?cpage=%d&commentsort=%s#comments" % (cp, sort)
            r, ptxt = C.fetch(sess, purl, pacer, tries=4, ref=C.BASE + "/", timeout=45)
            if ptxt is None:
                continue
            for node in S03.extract_comments(LH.fromstring(ptxt)):
                if node.get("id") and node["id"] not in found:
                    found[node["id"]] = node
    return found, declared


def decorate_new(node):
    """新评论补 s04d/s06 依赖的头像占位字段（老评论不动）。"""
    node = dict(node)
    node.setdefault("avatar_local", AVATAR_PLACEHOLDER)
    node.setdefault("avatar_is_default", True)
    return node


def main():
    days, keys_path, dry = parse_args()
    scope = C.scope_read(keys_path)
    cut = (date.today() - timedelta(days=days)).isoformat() if days > 0 else None

    # 选帖：日期命中 或 在范围内
    targets = []
    for url, key, slot, meta, path in C.Y.iter_meta():
        d = meta.get("date") or ""
        by_date = bool(cut and d >= cut)
        by_scope = bool(scope and url in scope)
        if by_date or by_scope:
            targets.append((url, key, d, by_scope))
    targets.sort(key=lambda x: x[2], reverse=True)

    C.log("[s02c] 评论增量：cut=%s 范围=%d 篇 -> 目标 %d 篇%s"
          % (cut or "不限", len(scope) if scope else 0, len(targets), " (DRY)" if dry else ""))

    sess = C.make_session()
    pacer = C.Pacer(base=1.0, jitter=0.6, rest_every=40, rest_secs=3.0, name="comments")

    tot_new = 0
    tot_posts = 0
    t0 = time.time()
    for idx, (url, key, d, by_scope) in enumerate(targets, 1):
        parsed = C.Y.read_parsed(url=url, key=key)
        if parsed is None:
            continue
        old = parsed.get("comments") or []
        old_ids = set(str(c.get("id")) for c in old if isinstance(c, dict) and c.get("id"))
        # dry 也要抓一遍才知道新增多少（否则无法判断）——区别只在下方不写盘

        try:
            found, declared = fetch_all_comments(sess, pacer, url)
        except C.CircuitBreaker as e:
            C.log("[s02c] 熔断停止: %s" % e)
            C.state_append("comments", url, "CIRCUIT", msg=str(e))
            break

        if not found:
            # 抓不到评论：站点无评论区 / 渲染上限 / 网络失败，一律不改旧数据
            C.state_append("comments", url, "EMPTY", declared=declared or 0)
            continue

        new_nodes = [decorate_new(n) for cid, n in found.items() if cid not in old_ids]
        if not new_nodes:
            C.state_append("comments", url, "NOCHANGE", total=len(found))
            continue

        tot_posts += 1
        tot_new += len(new_nodes)
        if not dry:
            merged = list(old) + new_nodes
            parsed["comments"] = merged
            parsed["comments_parsed"] = len(merged)
            C.Y.write_parsed(url, parsed)
            if keys_path and scope and url not in scope:
                C.scope_add(keys_path, url)   # 让 s04d/s06 也重做这篇
        C.state_append("comments", url, "DRYNEW" if dry else "NEW",
                       added=len(new_nodes), total=len(found), declared=declared or 0)
        if idx % 20 == 0 or len(new_nodes) >= 10:
            C.log("[s02c] %d/%d %s +%d 新评论 (累计 +%d, %.0fs)"
                  % (idx, len(targets), url.rsplit("/", 1)[-1], len(new_nodes), tot_new, time.time() - t0))

    C.state_append("comments", "__summary__", "DONE", posts=tot_posts, added=tot_new,
                   targets=len(targets), dry=int(bool(dry)))
    C.log("[s02c] 完成：%d 篇有新增，共 +%d 条评论（只增不删，旧评论零改动）%s  用时 %.0fs"
          % (tot_posts, tot_new, " [DRY]" if dry else "", time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
