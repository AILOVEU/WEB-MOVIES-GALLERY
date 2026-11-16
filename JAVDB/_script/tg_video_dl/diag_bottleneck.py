# -*- coding: utf-8 -*-
"""定位真实瓶颈：逐条连接串行测速 + 代理原始带宽 + 传输层对比。

目的：判断 ~1 MB/s 的天花板是
  (a) 代理/链路带宽上限  -> 任何改法都无用，只能换节点
  (b) 单连接串行          -> 连接池有效
  (c) 并发连接被代理惩罚  -> 连接池有害，应保持单连接
"""
# [own] 分类=download 自写脚本（tg_video_dl 瓶颈定位）
import asyncio
import sys
import time
import types as pytypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

CHUNK = tg_dl.PART
NCHUNK = 8          # 每条连接取 4 MiB


async def stream(c, media, size, off, n, dc_id):
    got = 0
    async for ch in c.iter_download(media, offset=off, limit=n, chunk_size=CHUNK,
                                    request_size=CHUNK, file_size=size, dc_id=dc_id):
        got += len(ch)
    return got


async def main():
    a = pytypes.SimpleNamespace(api_id=None, api_hash=None,
                                proxy="127.0.0.1:7890:http", timeout=30)
    client = tg_dl.make_client(a, "tgdl")
    await client.connect()
    pool = await tg_dl.build_pool(client, a, 4)

    ent = await tg_dl.get_entity(client, "Sgp987")
    msg = await client.get_messages(ent, ids=7445)
    media, size = msg.media, msg.media.document.size
    dc_id = msg.media.document.dc_id
    print("媒体 msg 7445，%.1f MB，DC%s\n" % (size / 1048576, dc_id))

    # A. 逐条连接串行测速（每条独立跑，排除并发干扰）
    print("=== A. 逐条连接串行测速（每条 4 MiB）===")
    rates = []
    for i, c in enumerate(pool):
        t0 = time.time()
        n = await stream(c, media, size, i * 8 * CHUNK, NCHUNK, dc_id)
        d = time.time() - t0
        r = n / 1024 / d
        rates.append(r)
        print("  连接 %d: %5.1f MiB  %6.2f 秒  %7.0f KB/s" % (i, n / 1048576, d, r))
    print("  单连接平均 %.0f KB/s，最好 %.0f KB/s\n" % (sum(rates) / len(rates), max(rates)))

    # B. 同一连接上连续取 16 MiB（测单连接稳态吞吐）
    print("=== B. 单连接稳态（连续 16 MiB）===")
    t0 = time.time()
    n = await stream(pool[0], media, size, 0, 32, dc_id)
    d = time.time() - t0
    print("  %5.1f MiB  %6.2f 秒  %7.0f KB/s\n" % (n / 1048576, d, n / 1024 / d))

    # C. 4 连接真并发（同一时刻各取 4 MiB）
    print("=== C. 4 连接并发（各 4 MiB，同时开始）===")
    t0 = time.time()
    res = await asyncio.gather(*[
        stream(pool[i], media, size, (i + 8) * 8 * CHUNK, NCHUNK, dc_id) for i in range(4)])
    d = time.time() - t0
    tot = sum(res)
    print("  %5.1f MiB  %6.2f 秒  聚合 %7.0f KB/s" % (tot / 1048576, d, tot / 1024 / d))
    print("  （若聚合 ≈ 单连接，说明代理封顶；若聚合 ≈ 4x 单连接，说明连接池有效）\n")

    await tg_dl.close_pool(pool)


asyncio.run(main())
