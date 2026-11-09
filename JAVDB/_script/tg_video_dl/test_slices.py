# -*- coding: utf-8 -*-
"""验证并行分段：切分对齐、分段续传、合片顺序、看门狗。"""
# [own] 分类=download 自写脚本（tg_video_dl 下载器，原属 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁出仓库），非 Telegram 上游
import asyncio
import sys
import types as pytypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

TMPD = Path(__file__).parent / "_slice_out"
TMPD.mkdir(exist_ok=True)
checks = []


def check(label, got, want):
    ok = got == want
    checks.append(ok)
    print("%-30s got=%-18s want=%-18s %s" % (label, str(got), str(want), "OK" if ok else "FAIL"))


# ---------- slices_for 切分数学 ----------
P = tg_dl.PART
for size, n, want in [(4 * P, 4, [(0, P), (P, P), (2 * P, P), (3 * P, P)]),
                      (5 * P, 2, [(0, 3 * P), (3 * P, 2 * P)]),
                      (3 * P, 8, [(0, P), (P, P), (2 * P, P)]),   # n 超过段数则收敛
                      (P + 100, 2, [(0, P), (P, 100)]),           # 非整段尾巴
                      (0, 4, []), (-5, 4, [])]:
    got = tg_dl.slices_for(size, n)
    check("slices_for(%d,%d)" % (size, n), got, want)
    if want:
        check("  覆盖完整且连续", (got[0][0], sum(l for _, l in got), got[-1][0] + got[-1][1]),
              (0, size, size))

# 段边界必须 512KiB 对齐（iter_download 的 request_size 粒度）
big = 394 * 1024 * 1024 + 12345
segs = tg_dl.slices_for(big, 4)
check("大文件 4 段边界对齐", all(o % P == 0 for o, _ in segs[:-1]), True)
check("大文件 4 段总长相符", sum(l for _, l in segs), big)

# ---------- 假客户端：iter_download 按 offset/limit 出数据 ----------
DATA = bytes(range(256))


def fill(pos, n):
    """内容只取决于绝对位置（每 512KiB 块一个特征字节），可验证拼接顺序"""
    return bytes([(pos // P) % 251]) * n


def expected(size):
    return b"".join(fill(k, P) for k in range(0, size, P))


class FakeClient:
    """严格按 telethon 真实语义建模：
    offset = 字节偏移；limit = 最多产出多少个 chunk（不是字节数！）。
    这样当调用方误把字节数当 limit 传进来时，就会超写，被下面的断言拓住。
    """

    def __init__(self, stall=False):
        self.calls, self.stall = [], stall

    async def iter_download(self, media, *, offset=0, stride=None, limit=None,
                            chunk_size=None, request_size=524288, file_size=None, dc_id=None):
        self.calls.append((offset, limit))
        if self.stall:
            await asyncio.sleep(30)
            return
        cs = chunk_size or request_size
        nmax = limit if limit is not None else (10 ** 9)
        sent = 0
        pos = offset
        while sent < nmax and (file_size is None or pos < file_size):
            await asyncio.sleep(0)
            n = min(cs, file_size - pos) if file_size else cs
            if n <= 0:
                break
            yield fill(pos, n)
            pos += n
            sent += 1


async def run():
    media = pytypes.SimpleNamespace(document=pytypes.SimpleNamespace(dc_id=5))
    size = 6 * P          # 必须≥ parts×512KiB 才能真的切出多段

    # 1) 正常拉全部段 + 合片顺序
    item = {"name": "A-1.mp4", "size": size, "msg": pytypes.SimpleNamespace(media=media, id=1)}
    tmp = TMPD / "A-1.mp4.part"
    prog = {"done": 0}
    args = pytypes.SimpleNamespace(parts=3, stall=120)
    fc = FakeClient()
    await tg_dl.download_slices(fc, item, tmp, prog, args)
    check("3 段请求的 offset", sorted(c[0] for c in fc.calls), [0, 2 * P, 4 * P])
    check("limit 按分片个数传(非字节数)", sorted(set(c[1] for c in fc.calls)), [2])
    check("合片大小", tmp.stat().st_size, size)
    want = expected(size)
    check("合片内容顺序正确", tmp.read_bytes() == want, True)
    check("临时分段已清理", list(TMPD.glob("A-1.mp4.part.p*")), [])
    check("进度累计", prog["done"], size)

    # 2) 分段级断点续传：预先放好一个完整段，API 就不该再请求它
    tmp2 = TMPD / "B-1.mp4.part"
    segs = tg_dl.slices_for(size, 3)
    TMPD.joinpath("B-1.mp4.part.p1").write_bytes(expected(size)[segs[1][0]:segs[1][0] + segs[1][1]])
    prog2 = {"done": 0}
    fc2 = FakeClient()
    await tg_dl.download_slices(fc2, {"name": "B-1.mp4", "size": size,
                                     "msg": pytypes.SimpleNamespace(media=media, id=2)},
                                tmp2, prog2, args)
    check("已完成段被跳过(只请求 2 段)", len(fc2.calls), 2)
    check("跳过段计入进度", prog2["done"], size)
    check("B 合片内容正确", tmp2.read_bytes() == want, True)

    # 3) 超长残段（比段长还大）= 脏数据，必须清除整段重拉
    TMPD.joinpath("C-1.mp4.part.p0").write_bytes(expected(size)[:segs[0][1]] + b"junk")
    tmp3 = TMPD / "C-1.mp4.part"
    fc3 = FakeClient()
    await tg_dl.download_slices(fc3, {"name": "C-1.mp4", "size": size,
                                     "msg": pytypes.SimpleNamespace(media=media, id=3)},
                                tmp3, {"done": 0}, args)
    check("超长残段从头重拉", (min(c[0] for c in fc3.calls), fc3.calls[-1][1]), (0, 2))
    check("C 合片内容正确", tmp3.read_bytes() == want, True)

    # 4) 卡死必须被看门狗中断
    try:
        await tg_dl.download_slices(FakeClient(stall=True),
                                    {"name": "D-1.mp4", "size": size,
                                     "msg": pytypes.SimpleNamespace(media=media, id=4)},
                                    TMPD / "D-1.mp4.part", {"done": 0},
                                    pytypes.SimpleNamespace(parts=2, stall=3))
        check("卡死 -> CancelledError", "no-raise", "CancelledError")
    except asyncio.CancelledError:
        check("卡死 -> CancelledError", "CancelledError", "CancelledError")
    left = sorted(TMPD.glob("D-1.mp4.part.p*"))
    check("卡死后不删已开段(保留待续)", len(left), 2)
    check("卡死时零写入无脏数据", sum(f.stat().st_size for f in left), 0)

    # 5) 分段内字节级续传：半截段不得被删，只补拉剩下部分
    tmp5 = TMPD / "E-1.mp4.part"
    half = P                                      # 段长 2P，只预放一半，验证段内续传
    TMPD.joinpath("E-1.mp4.part.p0").write_bytes(expected(size)[:half])
    fc5 = FakeClient()
    prog5 = {"done": 0}
    await tg_dl.download_slices(fc5, {"name": "E-1.mp4", "size": size,
                                     "msg": pytypes.SimpleNamespace(media=media, id=5)},
                                tmp5, prog5, args)
    check("半截段从断点接着拉", min(c[0] for c in fc5.calls), half)
    check("半截段只补缺口那段", [c for c in fc5.calls if c[0] == half], [(half, 1)])
    check("续传共 3 请求", len(fc5.calls), 3)
    check("E 合片内容正确", tmp5.read_bytes() == want, True)
    check("E 进度含已有字节", prog5["done"], size)

    # 6) 回归：就算 iter_download 产出超过段长，也必须被硬截在段界上
    tmp6 = TMPD / "F-1.mp4.part"
    fc6 = FakeClient(stall=False)
    bad = pytypes.SimpleNamespace(parts=4, stall=90)
    await tg_dl.download_slices(fc6, {"name": "F-1.mp4", "size": size,
                                     "msg": pytypes.SimpleNamespace(media=media, id=6)},
                                tmp6, {"done": 0}, bad)
    check("F 合片尺寸恰等于总长", tmp6.stat().st_size, size)
    check("F 合片内容正确", tmp6.read_bytes() == want, True)

    # 7) 回归：重试时 prog 不归零就会双计（旧代码因此打印过 138%）
    tmp7 = TMPD / "G-1.mp4.part"
    TMPD.joinpath("G-1.mp4.part.p0").write_bytes(expected(size)[:P])
    prog7 = {"done": size}                 # 模拟上一次尝试留下的计数
    await tg_dl.download_slices(FakeClient(), {"name": "G-1.mp4", "size": size,
                                               "msg": pytypes.SimpleNamespace(media=media, id=7)},
                                tmp7, prog7, args)
    check("不归零会超总长(旧 bug)", prog7["done"] > size, True)
    prog7["done"] = 0                      # 修复后 download_one 每次尝试都这么做
    await tg_dl.download_slices(FakeClient(), {"name": "G-1.mp4", "size": size,
                                               "msg": pytypes.SimpleNamespace(media=media, id=7)},
                                tmp7, prog7, args)
    check("归零后恰等于总长", prog7["done"], size)
    check("G 合片内容正确", tmp7.read_bytes() == want, True)

    for f in list(TMPD.iterdir()):
        f.unlink()
    print("=" * 60)
    print("passed %d / %d" % (sum(checks), len(checks)))
    sys.exit(0 if all(checks) else 1)


asyncio.run(run())
