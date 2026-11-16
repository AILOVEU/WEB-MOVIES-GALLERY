# -*- coding: utf-8 -*-
"""对比测试：单连接 vs 多连接，同一媒体、同样字节数，看聚合吞吐是否随连接数上升。

这是判断「连接池到底有没有用」的决定性实验。
如果单连接和多连接吞吐相同 -> 瓶颈在代理/链路带宽，加连接无用。
如果随连接数线性上升 -> 瓶颈是单连接串行，连接池有效。
"""
# [own] 分类=download 自写脚本（tg_video_dl 连接池 A/B 对比）
import asyncio
import sys
import time
import types as pytypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

CHUNK = tg_dl.PART
PER_CONN = 8 * CHUNK          # 每条连接取 4 MiB


async def stream(c, media, size, off, nchunks, dc_id):
    got = 0
    async for ch in c.iter_download(media, offset=off, limit=nchunks, chunk_size=CHUNK,
                                    request_size=CHUNK, file_size=size, dc_id=dc_id):
        got += len(ch)
    return got


async def round_trip(pool, media, size, dc_id, n, label):
    """用 n 条连接各取 PER_CONN 字节，测聚合吞吐。"""
    t0 = time.time()
    res = await asyncio.gather(*[
        stream(pool[i], media, size, i * PER_CONN, PER_CONN // CHUNK, dc_id)
        for i in range(n)])
    d = time.time() - t0
    tot = sum(res)
    rate = tot / 1024 / d
    print("  %-22s %d 连接  共 %5.1f MiB  %6.2f 秒  %7.0f KB/s"
          % (label, n, tot / 1048576, d, rate))
    return rate


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

    print("=== 第一轮（冷启动，含 TLS/连接预热）===")
    r1 = await round_trip(pool, media, size, dc_id, 1, "单连接")
    r2 = await round_trip(pool, media, size, dc_id, 2, "双连接")
    r4 = await round_trip(pool, media, size, dc_id, 4, "四连接")

    print("\n=== 第二轮（预热后，结果更可信）===")
    r1b = await round_trip(pool, media, size, dc_id, 1, "单连接")
    r2b = await round_trip(pool, media, size, dc_id, 2, "双连接")
    r4b = await round_trip(pool, media, size, dc_id, 4, "四连接")

    print("\n=== 结论（用第二轮）===")
    print("  4 连接 / 1 连接 = %.2fx" % (r4b / r1b if r1b else 0))
    if r4b / r1b < 1.3:
        print("  -> 加连接几乎没提升：瓶颈在代理或链路带宽，不在单连接串行。")
        print("     连接池不会带来大幅提速，真实上限约 %.0f KB/s。" % r4b)
    else:
        print("  -> 连接数增加带来明显提升：单连接串行是瓶颈，连接池有效。")

    await tg_dl.close_pool(pool)


asyncio.run(main())
