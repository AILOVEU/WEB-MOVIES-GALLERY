# -*- coding: utf-8 -*-
"""交错测连接池收益：1/2/4 连接，同一网络状态下轮转，排除漂移。

之前测连接池时网络正好在低谷（单连接 966，4 连接 479），结论不可信。
这里每轮都跑 1/2/4 连接，让漂移同等作用于三组，再比中位数。
"""
# [own] 分类=download 自写脚本（tg_video_dl 连接池交错验证）
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
ROUNDS = 4
MSG_ID = 7445


async def stream(c, media, size, off, n, dc_id):
    got = 0
    async for ch in c.iter_download(media, offset=off, limit=n, chunk_size=CHUNK,
                                    request_size=CHUNK, file_size=size, dc_id=dc_id):
        got += len(ch)
    return got


async def run_n(pool, media, size, dc_id, n, base_off):
    """用 n 条连接并发各取 PER_CONN 字节，返回聚合 KB/s。"""
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
    pool = await tg_dl.build_pool(client, a, 4)

    ent = await tg_dl.get_entity(client, "Sgp987")
    msg = await client.get_messages(ent, ids=MSG_ID)
    media, size = msg.media, msg.media.document.size
    dc_id = msg.media.document.dc_id
    print("媒体 msg %d，%.1f MB，DC%s | 交错 %d 轮\n" % (MSG_ID, size / 1048576, dc_id, ROUNDS))

    samples = {1: [], 2: [], 4: []}
    for r in range(ROUNDS):
        off = (300 + r * 60) * CHUNK
        line = "  第%d轮  " % (r + 1)
        for n in (1, 2, 4):
            rate = await run_n(pool, media, size, dc_id, n, off)
            if rate:
                samples[n].append(rate)
                line += "%d连接 %6.0f KB/s   " % (n, rate)
            else:
                line += "%d连接   fail        " % n
        print(line)

    print("\n" + "=" * 60)
    med = {n: statistics.median(v) for n, v in samples.items() if v}
    for n, m in sorted(med.items()):
        print("  %d 连接  中位数 %7.0f KB/s   (n=%d)" % (n, m, len(samples[n])))

    if 1 in med and 4 in med:
        ratio = med[4] / med[1]
        print("\n  4 连接 / 1 连接 = %.2fx" % ratio)
        if ratio >= 1.5:
            print("  结论：连接池有效，多连接确实能提升聚合吞吐。")
        elif ratio >= 0.9:
            print("  结论：连接池几乎无收益，单连接已吃满带宽。")
        else:
            print("  结论：多连接反而更慢，代理对并发连接有惩罚。")

    await tg_dl.close_pool(pool)


asyncio.run(main())
