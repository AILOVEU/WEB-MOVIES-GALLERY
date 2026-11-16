# -*- coding: utf-8 -*-
"""交错 A/B 测量：排除网络漂移，确认 TcpAbridged 的优势是否真实。

上一轮是「一种传输层连跑两轮再换下一种」，网络中途变差就会污染结果。
这里改成 A/B/A/B 交错，让漂移同等作用于两种传输层，再比中位数。
"""
# [own] 分类=download 自写脚本（tg_video_dl 传输层交错验证）
import asyncio
import statistics
import sys
import time
import types as pytypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

from telethon import TelegramClient
from telethon.network import ConnectionTcpAbridged, ConnectionTcpFull, ConnectionTcpIntermediate

CHUNK = tg_dl.PART
NCHUNK = 16                 # 每次 8 MiB
ROUNDS = 5                  # 交错 5 轮
MSG_ID = 7445


async def measure(conn_cls, api_id, api_hash, proxy, media, size, dc_id, off):
    sess = str(tg_dl.HERE / "session" / "tgdl")
    client = TelegramClient(sess, api_id, api_hash, proxy=proxy, connection=conn_cls,
                            timeout=30, request_retries=3, connection_retries=3)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return None
        t0 = time.time()
        got = 0
        async for ch in client.iter_download(media, offset=off, limit=NCHUNK,
                                             chunk_size=CHUNK, request_size=CHUNK,
                                             file_size=size, dc_id=dc_id):
            got += len(ch)
        d = time.time() - t0
        return got / 1024 / d if d > 0 else None
    except Exception:
        return None
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def main():
    a = pytypes.SimpleNamespace(api_id=None, api_hash=None, proxy="127.0.0.1:7890:http")
    api_id, api_hash = tg_dl.load_creds(a)
    proxy = tg_dl.parse_proxy("127.0.0.1:7890:http")

    c0 = tg_dl.make_client(a, "tgdl")
    await c0.connect()
    ent = await tg_dl.get_entity(c0, "Sgp987")
    msg = await c0.get_messages(ent, ids=MSG_ID)
    media, size = msg.media, msg.media.document.size
    dc_id = msg.media.document.dc_id
    await c0.disconnect()
    print("媒体 msg %d，%.1f MB，DC%s | 交错 %d 轮，每轮 8 MiB\n"
          % (MSG_ID, size / 1048576, dc_id, ROUNDS))

    cases = [
        ("Abridged", ConnectionTcpAbridged),
        ("Intermediate", ConnectionTcpIntermediate),
        ("Full", ConnectionTcpFull),
    ]
    samples = {label: [] for label, _ in cases}

    for r in range(ROUNDS):
        off = (200 + r * 40) * CHUNK
        line = "  第%d轮  " % (r + 1)
        for label, cls in cases:
            rate = await measure(cls, api_id, api_hash, proxy, media, size, dc_id, off)
            if rate:
                samples[label].append(rate)
                line += "%-14s %6.0f KB/s   " % (label, rate)
            else:
                line += "%-14s %6s        " % (label, "fail")
        print(line)

    print("\n" + "=" * 62)
    med = {}
    for label, vals in samples.items():
        if vals:
            med[label] = statistics.median(vals)
    base = med.get("Intermediate")
    for label, m in sorted(med.items(), key=lambda kv: -kv[1]):
        x = ("%.2fx" % (m / base)) if base else "-"
        print("  %-14s 中位数 %7.0f KB/s   %s   (n=%d)"
              % (label, m, x, len(samples[label])))

    if "Abridged" in med and base:
        ratio = med["Abridged"] / base
        print()
        if ratio >= 1.5:
            print("  结论：Abridged 稳定快 %.1fx，是真实差异，不是噪声。" % ratio)
        else:
            print("  结论：Abridged 仅快 %.2fx，落在噪声范围内，不宜作为默认。" % ratio)


asyncio.run(main())
