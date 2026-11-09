# -*- coding: utf-8 -*-
"""
s02_fetch_html.py — 抓取文章原始 HTML
- 串行、限速、断点续传（已存在则跳过）；原料落点到**年度容器**的 raw 段：
  _pipeline/material/<年>/<url_key>.jsonl（years.py），不再写 _rawdata/html/
- 落槽：新文章先落 undated 槽，s03 解析出 date 后自动搬到正确年份
- 熔断：连续 8 次失败即停并写台账
- 支持 --limit N 抽样跑、--shard=i/n 分片并行
- --keys-file=<路径>：**本轮增量范围**（update.py 写的 update_keys.json）。给了就只处理
  这批 URL，不再对 7240 条清单逐篇 has_raw（那要开七千个容器）；不给 = 全树，行为与历史一致
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C


def main():
    limit = None
    shard = None
    for a in sys.argv[1:]:
        if a.startswith("--limit="):
            limit = int(a.split("=", 1)[1])
        elif a.startswith("--shard="):
            # --shard=i/n  只处理第 i 片（0 基），用于多进程并行
            i, n = a.split("=", 1)[1].split("/")
            shard = (int(i), int(n))

    urls = C.read_json(os.path.join(C.SRC, "urls.json"), {}) or {}
    if not urls:
        C.log("[s02] 致命：_pipeline/urls.json 为空，请先运行 s01")
        return 2
    items = sorted(urls.items(), key=lambda kv: (kv[1].get("lastmod", ""), kv[0]))
    scope = C.scope_read(C.scope_arg())
    if scope:
        # 范围模式：不先过滤就会对七千篇逐篇 has_raw（每篇开一次容器读 meta 行）
        items = [it for it in items if it[0] in scope]
        C.log("[s02] 范围模式：%d 篇（清单共 %d 篇，未逐篇查容器）"
              % (len(items), len(urls)))
    if limit:
        items = items[:limit]
    if shard:
        i, n = shard
        items = [it for k, it in enumerate(items) if k % n == i]
        C.log("[s02] 分片模式 i=%d n=%d" % (i, n))
    C.log("[s02] 待处理 %d 篇" % len(items))

    pacer = C.Pacer(base=1.2, jitter=0.8, rest_every=50, rest_secs=3.0, name="article")
    sess = C.make_session()
    ref = C.BASE + "/"

    if shard:
        i, n = shard
        # 分片进程：独立的限速状态文件，避免多进程互相覆盖 last_ts
        # 导致请求间隔计算失准；站点侧并发连接数 = 分片数，控制在 2 以内
        pacer = C.Pacer(base=2.0, jitter=1.0, rest_every=50, rest_secs=4.0,
                        name="article_sh%d" % i,
                        state_file=os.path.join(C.SRC, "pacer_sh%d.json" % i))

    done = 0
    skipped = 0
    failed = []
    t0 = time.time()

    for idx, (url, meta) in enumerate(items, 1):
        key = C.url_key(url)
        if C.Y.has_raw(url=url, key=key):
            skipped += 1
            continue

        try:
            r, txt = C.fetch(sess, url, pacer, tries=5, ref=ref, timeout=45)
        except C.CircuitBreaker as e:
            C.log("[s02] 熔断停止: %s" % e)
            C.state_append("html", url, "CIRCUIT", msg=str(e))
            break

        if r is None:
            failed.append(url)
            C.state_append("html", url, "FAIL")
            if pacer.fail_streak >= 8:
                C.log("[s02] 连续失败达上限，停止")
                break
            continue

        C.Y.write_raw(url, txt)
        done += 1
        C.state_append("html", url, "DONE", bytes=len(txt),
                       lastmod=meta.get("lastmod", ""))

        if done % 50 == 0:
            el = time.time() - t0
            rate = done / el if el else 0
            C.log("[s02] 已抓 %d / 跳过 %d / 失败 %d  (%.2f 篇/秒, 已用 %.0f 秒)"
                  % (done, skipped, len(failed), rate, el))

    C.log("[s02] 结束：本次抓取 %d，跳过 %d，失败 %d" % (done, skipped, len(failed)))
    if failed:
        C.log("[s02] 失败清单前 20 条:")
        for u in failed[:20]:
            C.log("    %s" % u)
        C.log("[s02] 重跑本脚本可自动续跑（已成功的不再请求）")
    C.state_append("html", "__summary__", "DONE", done=done, skipped=skipped, failed=len(failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
