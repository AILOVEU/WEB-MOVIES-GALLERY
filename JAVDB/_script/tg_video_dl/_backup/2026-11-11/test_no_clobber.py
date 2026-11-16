# -*- coding: utf-8 -*-
"""回归：磁盘上已存在同名视频时，download_one 绝不覆盖，只登记跳过（除非 --overwrite）。"""
# [own] 分类=download 自写脚本（tg_video_dl 下载器，原属 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁出仓库），非 Telegram 上游
import asyncio
import datetime
import json
import sys
import types as pytypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

TMPD = Path(__file__).parent / "_clobber_out"
TMPD.mkdir(exist_ok=True)
checks = []


def check(label, got, want):
    ok = got == want
    checks.append(ok)
    print("%-34s got=%-22s want=%-22s %s" % (label, str(got), str(want), "OK" if ok else "FAIL"))


SENTINEL = b"ALREADY-DOWNISHED-VIDEO" * 100        # 视为"已下载好的视频"


class FakeClient:
    def __init__(self):
        self.calls = 0

    async def iter_download(self, media, *, offset=0, stride=None, limit=None, chunk_size=None,
                            request_size=524288, file_size=None, dc_id=None):
        self.calls += 1
        yield b"x" * 4096

    async def get_messages(self, entity, ids=None):
        return None

    async def download_media(self, media, file=None, progress_callback=None):
        self.calls += 1


def mkitem(nbytes):
    msg = pytypes.SimpleNamespace(id=1, date=datetime.datetime(2026, 1, 1), media=None)
    return {"key": "777_1", "name": "AB-123.mp4", "size": nbytes, "msg": msg, "kind": "video"}


def mkargs(overwrite):
    return pytypes.SimpleNamespace(overwrite=overwrite, parts=1, stall=90, all=False,
                                   kinds="video,round,doc", watched="", codes="", min_rating=4.0)


async def main():
    dest = TMPD / "AB-123.mp4"
    dest.write_bytes(SENTINEL)
    ent = pytypes.SimpleNamespace(id=777)
    sem = asyncio.Semaphore(1)

    # 1) 默认：不覆盖，登记后跳过
    man = {}
    fc = FakeClient()
    r = await tg_dl.download_one(fc, ent, mkitem(9999999), TMPD, man, sem, mkargs(False))
    check("返回 skip", r, "skip")
    check("文件内容未被改动", dest.read_bytes() == SENTINEL, True)
    check("已登记进 manifest", man.get("777_1", {}).get("size"), len(SENTINEL))
    check("完全没请求网络", fc.calls, 0)
    check("manifest 已落盘", json.loads(tg_dl.manifest_path(TMPD).read_text(encoding="utf-8"))["777_1"]["size"],
          len(SENTINEL))

    # 2) 显式 --overwrite 才允许覆盖重下
    man2 = {}
    fc2 = FakeClient()
    item = mkitem(4096)
    r2 = await tg_dl.download_one(fc2, ent, item, TMPD, man2, sem, mkargs(True))
    check("overwrite 时确实重下", (r2, fc2.calls > 0), ("ok", True))
    check("文件被替换", dest.read_bytes() == b"x" * 4096, True)

    dest.unlink(missing_ok=True)
    tg_dl.manifest_path(TMPD).unlink(missing_ok=True)
    print("=" * 60)
    print("passed %d / %d" % (sum(checks), len(checks)))
    sys.exit(0 if all(checks) else 1)


asyncio.run(main())
