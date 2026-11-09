# -*- coding: utf-8 -*-
"""对比不同 MTProto 传输层在同一节点上的吞吐，找出被整形的那一层。

不新建 session、不导出授权，只是换 connection 类分别连接并取同样多的数据。
"""
# [own] 分类=download 自写脚本（tg_video_dl 下载器，原属 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁出仓库），非 Telegram 上游
import asyncio
from pathlib import Path
import sys
import time
import types

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl
from telethon.network import (ConnectionTcpFull, ConnectionTcpIntermediate,
                              ConnectionTcpObfuscated)

PART = 512 * 1024
MSG = 4480                      # MUDR-006，507.8 MiB
TOTAL = 16 * 1024 * 1024        # 每轮换 16 MiB
STREAMS = 4                     # 固定 4 条并发流，只换传输层


async def stream(client, media, size, off, nbytes, dc_id):
    got = 0
    async for ch in client.iter_download(media, offset=off, limit=(nbytes + PART - 1) // PART,
                                         chunk_size=PART, request_size=PART,
                                         file_size=size, dc_id=dc_id):
        got += len(ch)
    return got


async def one(name, conn):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from telethon import TelegramClient
    api_id, api_hash = tg_dl.load_creds(types.SimpleNamespace(api_id=None, api_hash=None))
    sess = str(Path(__file__).resolve().parent / "session" / "tgdl")   # 直接用已登录 session，串行使用无冲突
    client = TelegramClient(sess, api_id, api_hash,
                            proxy=tg_dl.parse_proxy("127.0.0.1:7890:http"),
                            connection=conn, request_retries=3, connection_retries=3)
    try:
        await client.connect()
        ent = await tg_dl.get_entity(client, "Sgp987")
        m = await client.get_messages(ent, ids=MSG)
        media, size = m.media, m.media.document.size
        dc_id = m.media.document.dc_id
        per = ((TOTAL // STREAMS + PART - 1) // PART) * PART
        t0 = time.time()
        r = await asyncio.gather(*[stream(client, media, size, i * per, per, dc_id)
                                   for i in range(STREAMS)])
        d = time.time() - t0
        rate = sum(r) / 1024 / d
        print("%-26s %8.1f KB/s" % (name, rate))
        await client.disconnect()
        return rate
    except Exception as e:
        print("%-26s 失败 %s: %s" % (name, type(e).__name__, str(e)[:70]))
        try:
            await client.disconnect()
        except Exception:
            pass
        return 0.0


async def main():
    print("同一节点、同一条 session、%d 条并发流取 %.0f MiB，只换传输层：\n"
          % (STREAMS, TOTAL / 1048576))
    base = await one("Intermediate(当前默认)", ConnectionTcpIntermediate)
    obf = await one("TcpObfuscated(伪装TLS)", ConnectionTcpObfuscated)
    full = await one("TcpFull", ConnectionTcpFull)
    print("\n结论: 默认 %.0f -> 伪装TLS %.0f (%.1fx) -> TcpFull %.0f (%.1fx)"
          % (base, obf, (obf / base if base else 0), full, (full / base if base else 0)))


asyncio.run(main())
