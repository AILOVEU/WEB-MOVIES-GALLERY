# -*- coding: utf-8 -*-
"""实测连接池：真连 Telegram，验证多条独立连接能否建起来并各自可用。

只建池、只做一次极小的 GetFile 探针，不下载大文件。
"""
# [own] 分类=download 自写脚本（tg_video_dl 连接池实测探针）
import asyncio
import sys
import time
import types as pytypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl


async def main():
    a = pytypes.SimpleNamespace(api_id=None, api_hash=None,
                                proxy="127.0.0.1:7890:http", timeout=30)
    client = tg_dl.make_client(a, "tgdl")
    await client.connect()
    if not await client.is_user_authorized():
        print("未登录，先跑 --login")
        return

    t0 = time.time()
    pool = await tg_dl.build_pool(client, a, 4)
    print("建池耗时 %.2f 秒，池大小 %d" % (time.time() - t0, len(pool)))

    # 每条连接做一次独立的小请求，证明各自真的可用
    for i, c in enumerate(pool):
        try:
            me = await c.get_me()
            print("  连接 %d: 可用 (me=%s, session_dc=%s)"
                  % (i, getattr(me, "username", None) or getattr(me, "id", "?"),
                     c.session.dc_id))
        except Exception as e:
            print("  连接 %d: 失败 %s: %s" % (i, type(e).__name__, str(e)[:80]))

    # 并发取同一文件的不同偏移，验证真并行（各自独立连接）
    ent = await tg_dl.get_entity(client, "Sgp987")
    msgs = await client.get_messages(ent, limit=20)
    target = None
    for m in msgs:
        k = tg_dl.media_kind(m)
        if k and k[1] > 8 * 1024 * 1024:
            target = (m, k)
            break
    if not target:
        print("最近 20 条里没有 >8MB 的视频，跳过并行探针")
        await tg_dl.close_pool(pool)
        return

    msg, kind = target
    media = msg.media
    size = kind[1]
    dc_id = getattr(getattr(media, "document", None), "dc_id", None)
    print("探针媒体：msg %s，%.1f MB，DC%s" % (msg.id, size / 1048576, dc_id))

    CHUNK = tg_dl.PART
    N = min(len(pool), 4)

    async def probe(c, off):
        got = 0
        async for ch in c.iter_download(media, offset=off, limit=4, chunk_size=CHUNK,
                                        request_size=CHUNK, file_size=size, dc_id=dc_id):
            got += len(ch)
        return got

    t1 = time.time()
    res = await asyncio.gather(*[probe(pool[i], i * CHUNK * 4) for i in range(N)])
    d = time.time() - t1
    print("并行探针：%d 条连接各取 2 MiB，共 %.1f MiB，耗时 %.2f 秒，聚合 %.0f KB/s"
          % (N, sum(res) / 1048576, d, sum(res) / 1024 / d))
    print("（这只是小样本探针，真实吞吐要看完整文件下载）")

    await tg_dl.close_pool(pool)


asyncio.run(main())
