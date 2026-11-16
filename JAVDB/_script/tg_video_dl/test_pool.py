# -*- coding: utf-8 -*-
"""验证连接池：分段是否真的派发到不同连接、单连接时是否退化为串行、合片是否正确。

这是本次提速改动的核心回归测试。旧版 --parts 无效的根因是
所有分段共用同一条 TCP 连接（Telethon 单连接严格串行），
所以这里的关键断言是「不同分段落在不同 client 上」。
"""
# [own] 分类=download 自写脚本（tg_video_dl 连接池回归测试）
import asyncio
import sys
import types as pytypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

TMPD = Path(__file__).parent / "_pool_out"
TMPD.mkdir(exist_ok=True)
checks = []

P = tg_dl.PART
PART = P


def check(label, got, want):
    ok = got == want
    checks.append(ok)
    print("%-34s got=%-20s want=%-20s %s" % (label, str(got), str(want), "OK" if ok else "FAIL"))


def fill(pos, n):
    """内容只取决于绝对位置（每 512KiB 块一个特征字节），与分片大小无关，可验证拼接顺序"""
    out = bytearray()
    i = 0
    while i < n:
        blk = (pos + i) // P
        take = min(n - i, P - ((pos + i) % P))
        out += bytes([blk % 251]) * take
        i += take
    return bytes(out)


def expected(size):
    return b"".join(fill(k, P) for k in range(0, size, P))


class FakeClient:
    """记录每个 client 实际拉过的分段偏移，用来验证派发是否分散。"""

    def __init__(self, name):
        self.name = name
        self.offsets = []
        self.bytes = 0

    async def iter_download(self, media, *, offset=0, stride=None, limit=None,
                            chunk_size=None, request_size=524288, file_size=None, dc_id=None):
        self.offsets.append(offset)
        cs = chunk_size or request_size
        nmax = limit if limit is not None else (10 ** 9)
        sent, pos = 0, offset
        while sent < nmax and (file_size is None or pos < file_size):
            await asyncio.sleep(0)
            n = min(cs, file_size - pos) if file_size else cs
            if n <= 0:
                break
            self.bytes += n
            yield fill(pos, n)
            pos += n
            sent += 1


async def run():
    media = pytypes.SimpleNamespace(document=pytypes.SimpleNamespace(dc_id=5))
    size = 8 * P

    # 1) 4 条连接的池 + 4 段：每段应落在不同连接上
    pool = [FakeClient(i) for i in range(4)]
    item = {"name": "P-1.mp4", "size": size,
            "msg": pytypes.SimpleNamespace(media=media, id=1)}
    tmp = TMPD / "P-1.mp4.part"
    prog = {"done": 0}
    args = pytypes.SimpleNamespace(parts=4, stall=120, chunk=PART)
    await tg_dl.download_slices(pool, item, tmp, prog, args)

    used = [c for c in pool if c.offsets]
    check("4 段用满 4 条连接", len(used), 4)
    check("每条连接各拉 1 段", [len(c.offsets) for c in pool], [1, 1, 1, 1])
    check("4 条连接偏移互不相同", len(set(c.offsets[0] for c in used)), 4)
    check("合片大小", tmp.stat().st_size, size)
    check("合片内容顺序正确", tmp.read_bytes() == expected(size), True)
    check("进度累计", prog["done"], size)
    check("临时分段已清理", list(TMPD.glob("P-1.mp4.part.p*")), [])

    # 2) 段数多于连接数：连接应被轮转复用，且不丢段
    pool2 = [FakeClient(i) for i in range(2)]
    tmp2 = TMPD / "P-2.mp4.part"
    a2 = pytypes.SimpleNamespace(parts=6, stall=120, chunk=PART)
    await tg_dl.download_slices(pool2, {"name": "P-2.mp4", "size": size,
                                       "msg": pytypes.SimpleNamespace(media=media, id=2)},
                                tmp2, {"done": 0}, a2)
    check("6 段请求收敛为 4 段(2P 对齐)", [len(c.offsets) for c in pool2], [2, 2])
    check("P-2 合片内容正确", tmp2.read_bytes() == expected(size), True)

    # 3) 单客户端（旧行为）：所有段共用一个 client，仍必须正确
    single = FakeClient("solo")
    tmp3 = TMPD / "P-3.mp4.part"
    await tg_dl.download_slices(single, {"name": "P-3.mp4", "size": size,
                                        "msg": pytypes.SimpleNamespace(media=media, id=3)},
                                tmp3, {"done": 0}, args)
    check("单连接拿到全部 4 段", len(single.offsets), 4)
    check("P-3 合片内容正确", tmp3.read_bytes() == expected(size), True)

    # 4) 池里连接数多于段数：多余连接应闲置，不报错
    pool4 = [FakeClient(i) for i in range(6)]
    tmp4 = TMPD / "P-4.mp4.part"
    a4 = pytypes.SimpleNamespace(parts=2, stall=120, chunk=PART)
    await tg_dl.download_slices(pool4, {"name": "P-4.mp4", "size": size,
                                       "msg": pytypes.SimpleNamespace(media=media, id=4)},
                                tmp4, {"done": 0}, a4)
    check("2 段只用 2 条连接", len([c for c in pool4 if c.offsets]), 2)
    check("P-4 合片内容正确", tmp4.read_bytes() == expected(size), True)

    # 5) 池里某条连接抛错：整体应失败，但已写分段保留待续
    class Boom(FakeClient):
        async def iter_download(self, *a, **kw):
            self.offsets.append(kw.get("offset", 0))
            raise RuntimeError("boom")
            yield  # pragma: no cover

    pool5 = [FakeClient(0), Boom(1)]
    tmp5 = TMPD / "P-5.mp4.part"
    try:
        await tg_dl.download_slices(pool5, {"name": "P-5.mp4", "size": size,
                                           "msg": pytypes.SimpleNamespace(media=media, id=5)},
                                    tmp5, {"done": 0},
                                    pytypes.SimpleNamespace(parts=2, stall=120, chunk=PART))
        check("连接报错 -> 异常上抛", "no-raise", "raised")
    except RuntimeError:
        check("连接报错 -> 异常上抛", "raised", "raised")

    # 6) chunk 参数生效：1 MiB 分片时 limit 个数应减半
    big = FakeClient("big")
    tmp6 = TMPD / "P-6.mp4.part"
    a6 = pytypes.SimpleNamespace(parts=1, stall=120, chunk=1024 * 1024)
    await tg_dl.download_slices(big, {"name": "P-6.mp4", "size": size,
                                     "msg": pytypes.SimpleNamespace(media=media, id=6)},
                                tmp6, {"done": 0}, a6)
    check("1MiB 分片合片大小", tmp6.stat().st_size, size)
    check("1MiB 分片内容正确", tmp6.read_bytes() == expected(size), True)

    for f in list(TMPD.iterdir()):
        if f.is_file():
            f.unlink()
    print("=" * 62)
    print("passed %d / %d" % (sum(checks), len(checks)))
    sys.exit(0 if all(checks) else 1)


asyncio.run(run())
