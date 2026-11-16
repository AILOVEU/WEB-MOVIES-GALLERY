# -*- coding: utf-8 -*-
"""端到端真实下载验证：用新脚本完整下一个文件，报告实际吞吐。

这是唯一能证明改动有效的测试 —— 前面都是分段探针，这里走完整 download_one 路径：
连接池 + 分段 + 合片 + manifest 落盘。
"""
# [own] 分类=download 自写脚本（tg_video_dl 端到端验证）
import asyncio
import sys
import time
import types as pytypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

MSG_ID = 7445
OUT = Path(__file__).parent / "_e2e_out"


async def main():
    OUT.mkdir(exist_ok=True)
    a = pytypes.SimpleNamespace(api_id=None, api_hash=None,
                                proxy="127.0.0.1:7890:http", timeout=30)
    client = tg_dl.make_client(a, "tgdl")
    await client.connect()
    ent = await tg_dl.get_entity(client, "Sgp987")
    msg = await client.get_messages(ent, ids=MSG_ID)
    kind = tg_dl.media_kind(msg)
    size = kind[1]
    print("目标 msg %d，%.1f MB" % (MSG_ID, size / 1048576))

    man_path = OUT / "manifest.json"
    man = {}
    args = pytypes.SimpleNamespace(
        conns=6, parts=6, chunk=tg_dl.PART, stall=120, workers=1,
        overwrite=True, man_path=str(man_path), timeout=30)
    args.man_lock = asyncio.Lock()

    pool = await tg_dl.build_pool(client, a, args.conns)

    item = {"key": "%d_%d" % (ent.id, MSG_ID), "kind": kind, "msg": msg,
            "size": size, "name": "E2E-TEST.mp4"}
    sem = asyncio.Semaphore(1)

    t0 = time.time()
    r = await tg_dl.download_one(pool, ent, item, OUT, man, sem, args)
    d = time.time() - t0

    dest = OUT / "E2E-TEST.mp4"
    actual = dest.stat().st_size if dest.exists() else 0
    print("\n结果: %s" % r)
    print("耗时 %.1f 秒，实际落盘 %.1f MB，平均 %.0f KB/s"
          % (d, actual / 1048576, actual / 1024 / d if d else 0))
    print("大小校验: %s (期望 %d, 实得 %d)"
          % ("OK" if actual == size else "FAIL", size, actual))
    print("manifest 记录: %s" % ("OK" if item["key"] in man else "FAIL"))

    await tg_dl.close_pool(pool)


asyncio.run(main())
