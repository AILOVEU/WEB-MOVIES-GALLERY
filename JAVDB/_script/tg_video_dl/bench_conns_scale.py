# -*- coding: utf-8 -*-
"""连接数扩展性：4/6/8/12 连接，找聚合吞吐的甜点与拐点。

已知 1->3.2 MB/s、2->6.0、4->11.2，接近线性。继续加是否还涨？
涨到哪开始被服务端或代理惩罚？这决定 --conns 的默认值。
"""
# [own] 分类=download 自写脚本（tg_video_dl 连接数扩展性）
import asyncio
import statistics
import sys
import time
import types as pytypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

CHUNK = tg_dl.PART
PER_CONN = 8 * CHUNK        # 每条连接 4 MiB
ROUNDS = 3
MSG_ID = 7445
MAX_CONNS = 12


async def stream(c, media, size, off, n, dc_id):
    got = 0
    async for ch in c.iter_download(media, offset=off, limit=n, chunk_size=CHUNK,
                                    request_size=CHUNK, file_size=size, dc_id=dc_id):
        got += len(ch)
    return got


async def run_n(pool, media, size, dc_id, n, base_off):
    t0 = time.time()
    res = await asyncio.gather(*[
        stream(pool[i], media, size, base_off + i * PER_CONN, PER_CONN // CHUNK, dc_id)
        for i in range(n)])
    d = time.time() - t0
    return sum(res) / 1024 / d if d > 0 else None


async def main():
    a = pytypes.SimpleNamespace(api_id=None, api_hash=None,
                                proxy="127.0.0.1:7890:http", timeout=30)
    client = tg_dl.make_client(a, "tgdl")
    await client.connect()
    pool = await tg_dl.build_pool(client, a, MAX_CONNS)

    ent = await tg_dl.get_entity(client, "Sgp987")
    msg = await client.get_messages(ent, ids=MSG_ID)
    media, size = msg.media, msg.media.document.size
    dc_id = msg.media.document.dc_id
    print("媒体 msg %d，%.1f MB，DC%s | 池 %d 条 | 交错 %d 轮\n"
          % (MSG_ID, size / 1048576, dc_id, len(pool), ROUNDS))

    levels = [4, 6, 8, 12]
    levels = [n for n in levels if n <= len(pool)]
    samples = {n: [] for n in levels}

    for r in range(ROUNDS):
        off = (500 + r * 120) * CHUNK
        line = "  第%d轮  " % (r + 1)
        for n in levels:
            rate = await run_n(pool, media, size, dc_id, n, off)
            if rate:
                samples[n].append(rate)
                line += "%2d连接 %6.0f   " % (n, rate)
            else:
                line += "%2d连接   fail    " % n
        print(line)

    print("\n" + "=" * 58)
    med = {n: statistics.median(v) for n, v in samples.items() if v}
    best = max(med.items(), key=lambda kv: kv[1]) if med else (None, 0)
    for n, m in sorted(med.items()):
        per = m / n
        mark = "  <-- 最佳" if n == best[0] else ""
        print("  %2d 连接  中位数 %7.0f KB/s   单连接均摊 %6.0f KB/s%s" % (n, m, per, mark))

    if med:
        print("\n  相对 4 连接：")
        base = med.get(4)
        for n, m in sorted(med.items()):
            if base:
                print("    %2d 连接  %.2fx" % (n, m / base))
        print("\n  建议 --conns %d（实测最佳）" % best[0])

    await tg_dl.close_pool(pool)


asyncio.run(main())
