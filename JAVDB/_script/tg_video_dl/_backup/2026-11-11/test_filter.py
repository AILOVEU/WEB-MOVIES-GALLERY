# -*- coding: utf-8 -*-
"""用 Sgp987 群里抓到的真实消息验证番号识别与命名。"""
# [own] 分类=download 自写脚本（tg_video_dl 下载器，原属 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁出仓库），非 Telegram 上游
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_dl

checks = []


def check(label, got, want):
    ok = got == want
    checks.append(ok)
    print("%-34s got=%-20s want=%-20s %s" % (label, str(got), str(want), "OK" if ok else "FAIL"))


# ---- 真实番号帖（正文里被反引号包住）----
check("code-JUL-804", tg_dl.find_code("水果派 261019E / 看到女婿 / 番号：`JUL-804` / #叶爱"), "JUL-804")
check("code-GVG-736", tg_dl.find_code("水果派 261020B / 屁孩 / 番号：`GVG-736` / #宝田萌奈美"), "GVG-736")
check("code-UMD-978", tg_dl.find_code("水果派 261020A / 少妇 / 番号：`UMD-978` / #素人"), "UMD-978")
check("code-DASS-946", tg_dl.find_code("水果派 261019B / 电臀 / 番号：`DASS-946` / #美园和花"), "DASS-946")
check("code-ROE-010", tg_dl.find_code("水果派 261019H / 叮当猫 / 番号：`ROE-010` / #一色桃子"), "ROE-010")
check("code-SSIS123", tg_dl.find_code("番号：SSIS123 无连字符"), "SSIS-123")

# ---- 无番号帖必须返回 None ----
check("none-爱弓阿姨", tg_dl.find_code("水果派 261019M / 【爱弓阿姨出轨神作】 / 番号： / #爱弓凉 #NTR"), None)
check("none-少妇偷情", tg_dl.find_code("水果派 2610180 / 【少妇偷情】 / #少妇 #开档 #出轨"), None)
check("none-艳遇55", tg_dl.find_code("水果派 261019N / 【鱼虾·你的艳遇55】 / #人妻 #出轨NTR"), None)
# 真实赌博广告原文：链接里有 qsty8999 / 1820362.com / y30480.com，不得被误认为番号
check("none-广告qsty", tg_dl.find_code(
    "加入 [**每天狂撒500万红包**](https://t.me/qsty8999/924) ➡️奔驰E300 加入 **#122体育**"), None)
check("none-广告域名", tg_dl.find_code(
    "[**1820362.com**](https://1820362.com/) 人民币平台 / [**y30480.com**](https://y30480.com/) USDT台"), None)
check("none-广告2028", tg_dl.find_code(
    "[**20280633.com**](https://20280633.com/) 不限ip 免实名、免绑卡"), None)

# ---- 文档原名兵底：流水号/默认名不算，真番号名才算 ----
check("fallback-IMG_7939", tg_dl.code_from_name("IMG_7939.MP4"), None)
check("fallback-3.mp4", tg_dl.code_from_name("3.mp4"), None)
check("fallback-SSIS-123", tg_dl.code_from_name("SSIS-123.mp4"), "SSIS-123")

# ---- 手机默认名/流水号不得当番号 ----
check("junk-IMG_7939", bool(tg_dl.JUNK_STEM_RX.match("IMG_7939")), True)
check("junk-3", bool(tg_dl.JUNK_STEM_RX.match("3")), True)
check("junk-2028", bool(tg_dl.JUNK_STEM_RX.match("2028")), True)
check("junk-VID_1234", bool(tg_dl.JUNK_STEM_RX.match("VID_1234")), True)
check("realname-SSIS-123", bool(tg_dl.JUNK_STEM_RX.match("SSIS-123")), False)
check("realname-show", bool(tg_dl.JUNK_STEM_RX.match("show")), False)

# ---- 命名：结果就是 番号.mp4 ----
seen = set()
check("name-8.mp4+JUL-804",
      tg_dl.build_name("8.mp4", "水果派 261019E / 番号：`JUL-804`", 7069, ".mp4", True, seen), "JUL-804.mp4")
check("name-11.mp4+GVG-736",
      tg_dl.build_name("11.mp4", "番号：`GVG-736`", 7068, ".mp4", True, seen), "GVG-736.mp4")
check("name-IMG_7939 无番号跳过",
      tg_dl.build_name("IMG_7939.MP4", "TG玩家首选全网最大信誉台 奔驰E300", 7078, ".mp4", True, seen), None)
check("name-3.mp4 无番号跳过",
      tg_dl.build_name("3.mp4", "水果派 261019M / 番号： / #爱弓凉", 7071, ".mp4", True, seen), None)
check("name-番号在原名里",
      tg_dl.build_name("SSIS-123.mp4", "本片介绍", 7080, ".mp4", True, seen), "SSIS-123.mp4")
check("name-广告帖跳过",
      tg_dl.build_name("2028.mp4", "[**20280633.com**](https://20280633.com/) 不限ip", 7029, ".mp4", True, seen), None)
# 同番号重复发布 -> 不覆盖
check("dedup-a", tg_dl.build_name("1.mp4", "番号：`MIDE-100`", 8001, ".mp4", True, seen), "MIDE-100.mp4")
check("dedup-b", tg_dl.build_name("2.mp4", "番号：`MIDE-100`", 8002, ".mp4", True, seen), "MIDE-100_8002.mp4")
# --all 兜底：无番号也给个可识别名字
check("all-兜底", tg_dl.build_name("IMG_7939.MP4", "赌博广告", 7078, ".mp4", False, set()), "msg007078.mp4")

# ---- 关注表筛选（watched.js 真实数据）----
WATCH = r"D:\workspace\z_WEB-MOVIES-GALLERY\JAVDB\data\site\watched.js"
check("compact", tg_dl.compact(" SSIS-338 "), "SSIS338")
wanted = tg_dl.load_watched(WATCH, 4.0)
import re as _re
_pairs = _re.findall(r'"([^"]+)"\s*:\s*([0-9.]+)', open(WATCH, encoding="utf-8").read())
HI = next(c for c, r in _pairs if float(r) >= 4.0)     # 运行时取高分样本
LO = next(c for c, r in _pairs if float(r) < 4.0)      # 运行时取低分样本（应被排除）
check("watched 已加载", len(wanted) > 20, True)
check("高分样本入选 %s" % HI, tg_dl.compact(HI) in wanted, True)
check("低分样本被排 %s" % LO, tg_dl.compact(LO) in wanted, False)
# 高分番号：标记内精确抽中
check("watch-hit", tg_dl.match_watched("水果派 / 番号：`%s` / #标签" % HI, "", wanted), HI)
# 无连字符 / 小写也能对上
check("watch-hit-nodash", tg_dl.match_watched("番号：%s" % tg_dl.compact(HI).lower(), "", wanted), HI)
# 评分不足（3.0）不算命中
check("watch-skip-lowrate", tg_dl.match_watched("番号：`%s`" % LO, "", wanted), None)
# 不在关注表里
check("watch-skip-unknown", tg_dl.match_watched("番号：`ZZZZ-99999`", "", wanted), None)
# 赌博广告不应误命中
check("watch-skip-ad", tg_dl.match_watched(
    "加入 [**红包**](https://t.me/qsty8999/924) 奔驰E300", "2028.mp4", wanted), None)
# 满分 5 的也要
check("watch-hit-score5", tg_dl.match_watched("番号：`MNGS-067`", "", wanted), "MNGS-067")

print("=" * 70)
print("passed %d / %d" % (sum(checks), len(checks)))
sys.exit(0 if all(checks) else 1)