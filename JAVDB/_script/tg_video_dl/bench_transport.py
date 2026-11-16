# -*- coding: utf-8 -*-
"""只换传输层、固定单流，测同一节点上的真实吞吐。

为什么单流：已实测该代理带宽上限约 994 KB/s，单连接就能吃满，
多流会互相抢带宽、把传输层的差异淹没在噪声里。只换传输层这一个变量。

对比：Intermediate（Telethon 默认）/ TcpFull / TcpObfuscated（伪装 TLS）/ TcpAbridged
"""
# [own] 分类=download 自写脚本（tg_video_dl 传输层对比）
import asyncio
import sys
import time
import types as pytypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

from telethon import TelegramClient
from telethon.network import (ConnectionTcpAbridged, ConnectionTcpFull,
                              ConnectionTcpIntermediate, ConnectionTcpObfuscated)

CHUNK = tg_dl.PART
NCHUNK = 16                 # 每次取 8 MiB
ROUNDS = 2                  # 每种传输层跑两轮，取最好成绩
MSG_ID = 7445               # 590.9 MB，DC5


async def measure(conn_cls, label, api_id, api_hash, proxy, media, size, dc_id, off):
    sess = str(tg_dl.HERE / "session" / "tgdl")
    client = TelegramClient(sess, api_id, api_hash, proxy=proxy, connection=conn_cls,
                            timeout=30, request_retries=3, connection_retries=3)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return None, "未授权"
        t0 = time.time()
        got = 0
        async for ch in client.iter_download(media, offset=off, limit=NCHUNK,
                                             chunk_size=CHUNK, request_size=CHUNK,
                                             file_size=size, dc_id=dc_id):
            got += len(ch)
        d = time.time() - t0
        return got / 1024 / d, None
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:70])
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def main():
    a = pytypes.SimpleNamespace(api_id=None, api_hash=None, proxy="127.0.0.1:7890:http")
    api_id, api_hash = tg_dl.load_creds(a)
    proxy = tg_dl.parse_proxy("127.0.0.1:7890:http")

    # 先用默认客户端拿媒体信息
    c0 = tg_dl.make_client(a, "tgdl")
    await c0.connect()
    ent = await tg_dl.get_entity(c0, "Sgp987")
    msg = await c0.get_messages(ent, ids=MSG_ID)
    media, size = msg.media, msg.media.document.size
    dc_id = msg.media.document.dc_id
    await c0.disconnect()
    print("媒体 msg %d，%.1f MB，DC%s | 每次取 %.0f MiB，每种跑 %d 轮\n"
          % (MSG_ID, size / 1048576, dc_id, NCHUNK * CHUNK / 1048576, ROUNDS))

    cases = [
        (ConnectionTcpIntermediate, "Intermediate(默认)"),
        (ConnectionTcpFull, "TcpFull"),
        (ConnectionTcpObfuscated, "TcpObfuscated(伪装TLS)"),
        (ConnectionTcpAbridged, "TcpAbridged"),
    ]

    results = {}
    for conn_cls, label in cases:
        best, err = None, None
        for r in range(ROUNDS):
            off = (100 + r * 40) * CHUNK      # 每轮换偏移，避开缓存
            rate, e = await measure(conn_cls, label, api_id, api_hash, proxy,
                                    media, size, dc_id, off)
            if e:
                err = e
                break
            best = rate if best is None else max(best, rate)
            print("  %-22s 第%d轮 %7.0f KB/s" % (label, r + 1, rate))
        if err:
            print("  %-22s 失败 %s" % (label, err))
        else:
            results[label] = best
            print("  %-22s 最好 %7.0f KB/s\n" % (label, best))

    if results:
        base = results.get("Intermediate(默认)")
        print("=" * 58)
        for label, rate in sorted(results.items(), key=lambda kv: -kv[1]):
            x = ("%.2fx" % (rate / base)) if base else "-"
            print("  %-22s %7.0f KB/s   %s" % (label, rate, x))
        win = max(results.items(), key=lambda kv: kv[1])
        print("\n最快：%s，%.0f KB/s" % (win[0], win[1]))
        if base and win[1] / base < 1.15:
            print("提升 < 15%%，在噪声范围内：换传输层没有实质帮助，瓶颈仍是代理带宽。")


asyncio.run(main())
