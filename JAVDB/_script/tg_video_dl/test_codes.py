# -*- coding: utf-8 -*-
"""验证 --codes 清单来源：真实 videos 目录 + videos_manifest.json。"""
# [own] 分类=download 自写脚本（tg_video_dl 下载器，原属 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁出仓库），非 Telegram 上游
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

JAVDB = r"D:\workspace\z_WEB-MOVIES-GALLERY\JAVDB"
checks = []


def check(label, got, want):
    ok = got == want
    checks.append(ok)
    print("%-36s got=%-14s want=%-14s %s" % (label, str(got), str(want), "OK" if ok else "FAIL"))


w = tg_dl.load_codes(JAVDB + r"\assets\videos")
print("videos 目录抽到番号 %d 个" % len(w))
check("数量 = 740 文件 - 5 个纯工作室名", len(w), 735)
check("legsjapan-1147 含数字所以保留", "LEGSJAPAN1147" in w, True)
check("含 ABP566", "ABP566" in w, True)
check("含老式 032715001", "032715001" in w, True)
check("排除纯工作室名 Blacked", "BLACKED" in w, False)
check("排除纯工作室名 Tushy", "TUSHY" in w, False)
check("全部 key 含数字", all(any(c.isdigit() for c in k) for k in w), True)
check("全部 value 大写", all(v == v.upper() for v in w.values()), True)

# 匹配：真实帖子里的可能写法
check("匹配 番号：ABP-566", tg_dl.match_watched("水果派 / 番号：`ABP-566` / #标签", "", w), "ABP-566")
check("匹配 无连字符小写", tg_dl.match_watched("番号：abp566 高清", "", w), "ABP-566")
check("匹配 带空格", tg_dl.match_watched("番号：ABP 566", "", w), "ABP-566")
check("匹配 老式下划线号", tg_dl.match_watched("番号：032715-001", "", w), "032715_001")
check("匹配 原名兜底", tg_dl.match_watched("无正文", "ABP-566.mp4", w), "ABP-566")
check("不匹配 未收录番号", tg_dl.match_watched("番号：`ZZZ-999`", "", w), None)
check("不匹配 赌博广告", tg_dl.match_watched(
    "[**1820362.com**](https://1820362.com/) 人民币平台 ABP 也好玩", "", w), None)

# 短 key 保护：4 个字符以下的番号不参与宽松匹配，避免撞广告文案
short = {"AB12": "AB-12", "ABCD5678": "ABCD-5678"}
check("短 key 不参与宽松匹配", tg_dl.match_watched("xxAB12xx", "", short), None)
check("长 key 宽松匹配生效", tg_dl.match_watched("看 xABCD5678x 这部", "", short), "ABCD-5678")

# JSON 来源
wj = tg_dl.load_codes(JAVDB + r"\data\state\videos_manifest.json")
print("videos_manifest.json 抽到番号 %d 个（含未 present 的历史条目）" % len(wj))
check("JSON 来源数量 > 目录来源", len(wj) > len(w), True)
check("JSON 也含 ABP566", "ABP566" in wj, True)

# 同番号重复发布只取一个（按消息由新到旧）
import asyncio
import datetime
import types as pytypes
from telethon import types as tt


def vmsg(mid, cap, fname):
    attrs = [tt.DocumentAttributeVideo(duration=1.0, w=640, h=360, supports_streaming=True)]
    if fname:
        attrs.append(tt.DocumentAttributeFilename(file_name=fname))
    doc = tt.Document(id=mid, access_hash=mid, file_reference=b"r", date=datetime.datetime(2026, 1, 1),
                      mime_type="video/mp4", size=5 * 1048576, dc_id=5, attributes=attrs)
    return pytypes.SimpleNamespace(id=mid, date=datetime.datetime(2026, 1, 1), text=cap,
                                   file=doc, media=tt.MessageMediaDocument(document=doc))


class FC:
    def __init__(self, msgs):
        self.msgs = msgs

    async def iter_messages(self, entity, limit=None):
        for m in self.msgs:
            yield m


async def dup_case(src):
    msgs = [vmsg(10, "番号：`IPX-528`", "a.mp4"), vmsg(9, "随便什么", "IPX-528.mp4"),
            vmsg(8, "番号：MIDE-674", "b.mp4")]
    ent = pytypes.SimpleNamespace(id=777)
    args = pytypes.SimpleNamespace(kinds="video,round,doc", latest=0, scan=0, all=False,
                                   codes=src, watched="", min_rating=4.0)
    return await tg_dl.collect(FC(msgs), ent, args)


items = asyncio.run(dup_case(JAVDB + r"\assets\videos"))
names = [i["name"] for i in items]
print("去重后取得:", names)
check("同番号只取一个", len([n for n in names if n.startswith("IPX-528")]), 1)
check("两个不同番号共 2 个", len(names), 2)
check("取的是最新那条消息", [i["msg"].id for i in items], [10, 8])

print("=" * 60)
print("passed %d / %d" % (sum(checks), len(checks)))
sys.exit(0 if all(checks) else 1)
