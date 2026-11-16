# -*- coding: utf-8 -*-
"""判定单连接吞吐瓶颈类型：随并发流数是线性增长（请求串行）还是封顶（隧道限速）。

只统计字节数、不落盘。用当前正在下的那个文件的媒体。
"""
# [own] 分类=download 自写脚本（tg_video_dl 下载器，原属 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁出仓库），非 Telegram 上游
import asyncio
from pathlib import Path
import sys
import time
import types

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

PART = 512 * 1024
TOTAL = 24 * 1024 * 1024          # 每轮共取 24 MiB
DC_ID = None


def make_client():
    a = types.SimpleNamespace(api_id=None, api_hash=None, proxy="127.0.0.1:7890:http")
    return tg_dl.make_client(a, "tgdl")


async def stream(client, media, size, off, nbytes, dc_id):
    got = 0
    async for ch in client.iter_download(media, offset=off, limit=(nbytes + PART - 1) // PART,
                                         chunk_size=PART, request_size=PART,
                                         file_size=size, dc_id=dc_id):
        got += len(ch)
    return got


async def main():
    client = make_client()
    await client.connect()
    ent = await tg_dl.get_entity(client, "Sgp987")
    m = await client.get_messages(ent, ids=4480)      # MUDR-006，507.8 MiB
    media = m.media
    size = media.document.size
    dc_id = media.document.dc_id
    print("测试媒体：msg 4480 (MUDR-006)，总长 %.1f MiB，存放于 DC%d，session DC=%d"
          % (size / 1048576, dc_id, client.session.dc_id))
    print("每轮取 %.0f MiB，分散到 N 条并发流\n" % (TOTAL / 1048576))
    print("%4s %10s %12s %14s" % ("N", "单流MiB", "耗时s", "聚合KB/s"))
    for n in (1, 2, 4, 8, 16, 32):
        per = TOTAL // n
        per = ((per + PART - 1) // PART) * PART
        t0 = time.time()
        r = await asyncio.gather(*[stream(client, media, size, i * per, per, dc_id) for i in range(n)])
        d = time.time() - t0
        tot = sum(r)
        print("%4d %10.2f %12.1f %14.0f" % (n, per / 1048576, d, tot / 1024 / d))
        if tot / 1024 / d > 1200:
            print("   （已超 1.2 MB/s，无需再测更高并发）")
            break
    await client.disconnect()


asyncio.run(main())
