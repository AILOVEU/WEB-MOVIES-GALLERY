# -*- coding: utf-8 -*-
"""验证卡死看门狗：假下载不推进应在 stall 秒内被判卡死；正常推进则顺利完成。"""
# [own] 分类=download 自写脚本（tg_video_dl 下载器，原属 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁出仓库），非 Telegram 上游
import asyncio
import sys
import time
import types as pytypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

OUT = Path(__file__).parent / "_stalltest.bin"
item = {"name": "X-1.mp4", "msg": pytypes.SimpleNamespace(media=None, id=1), "size": 1000}
checks = []


def check(label, got, want):
    ok = got == want
    checks.append(ok)
    print("%-34s got=%-14s want=%-14s %s" % (label, str(got), str(want), "OK" if ok else "FAIL"))


class FakeClient:
    """mode=stall 时收不到任何字节；mode=move 时每 0.2s 推进"""

    def __init__(self, mode):
        self.mode = mode

    async def download_media(self, media, file=None, progress_callback=None):
        if self.mode == "stall":
            await asyncio.sleep(30)          # 模拟死隧道：一直阻塞、零回调
            return file
        done = 0
        while done < 1000:
            await asyncio.sleep(0.2)
            done += 100
            if progress_callback:
                progress_callback(done, 1000)
        Path(file).write_bytes(b"x" * 1000)
        return file


async def run():
    # 1) 卡死必须被看门狗中断
    prog = {"done": 0}
    t0 = time.time()
    try:
        await tg_dl.guarded_download(FakeClient("stall"), item, str(OUT),
                                     lambda d, t: prog.update(done=d), prog, 3)
        check("stall -> 抛 CancelledError", "no-raise", "CancelledError")
    except asyncio.CancelledError:
        el = round(time.time() - t0, 1)
        check("stall -> 抛 CancelledError", "CancelledError", "CancelledError")
        check("stall 判定耗时约等于阈值", el < 8, True)

    # 2) 正常推进不能被误杀
    prog2 = {"done": 0}
    got = await tg_dl.guarded_download(FakeClient("move"), item, str(OUT),
                                       lambda d, t: prog2.update(done=d), prog2, 5)
    check("moving -> 正常返回", got, str(OUT))
    check("moving -> 字节确实进账", prog2["done"], 1000)
    check("moving -> 文件已落盘", OUT.exists(), True)

    OUT.unlink(missing_ok=True)
    print("=" * 60)
    print("passed %d / %d" % (sum(checks), len(checks)))
    sys.exit(0 if all(checks) else 1)


asyncio.run(run())
