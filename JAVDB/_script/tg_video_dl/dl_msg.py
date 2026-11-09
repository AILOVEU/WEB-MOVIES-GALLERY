# -*- coding: utf-8 -*-
"""按 msg id 定点下载指定消息的视频，绕开「一条消息只认一个番号」的匹配限制。

为什么需要它（2026-11-04 定位）：
  tg_dl 的 `match_watched` 一条消息**只返回一个番号**（先取「番号：」后的第一个，
  再按 wanted 字典顺序返回第一个命中的），而 `download_one` 的跳过判据是
  「该番号是否已下」。于是合集帖会被这样吞掉：
    msg 1383 正文 = 番号：SISS-985 SON-002 SONE-174 SONE-221 SSIS-928 SSIS-889（279 MB 一个视频）
    → 命中 SONE-221（清单内，且已由另一条 msg 1755 单独下过）
    → 日志打「跳过已下载 SONE-221」，同帖的 SONE-174 / SSIS-928 / SSIS-889 一起没拿到。
  这类番号用 --codes 永远跑不出来，必须按 msg id 定点下。

一个文件服务多个番号怎么处理：
  主文件名 = --primary 指定（默认取帖内第一个命中清单的番号），其余番号用 **NTFS 硬链接**
  落同名文件（不额外占空间），这样 media_scan.py 能把每个番号都关联到同一份视频。
  硬链接建不了（跨卷/权限）时回退复制，并在输出里说明多占了多少。

用法（在 tg_video_dl 目录下、主下载队列**停止后**再跑，别和 tg_dl 并发开两个 session）：
  .venv/Scripts/python.exe dl_msg.py --ids 1383 --dry-run
  .venv/Scripts/python.exe dl_msg.py --ids 1383 --alias SONE-174,SSIS-928,SSIS-889
"""
import argparse
import asyncio
import os
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import tg_dl

MB = 1048576
# 群目标：与 tg_dl.py 的 --target 默认值保持一致（那边是写死的字面量，没有导出常量）
DEFAULT_TARGET = "Sgp987"


def parse_args():
    ap = argparse.ArgumentParser(description="按 Telegram msg id 定点下载（合集帖专用）")
    ap.add_argument("--ids", required=True, help="消息 id，逗号分隔（如 1383 或 1383,2001）")
    ap.add_argument("--target", default=DEFAULT_TARGET, help="群用户名/链接")
    ap.add_argument("--out", default=tg_dl.OUT_DEFAULT, help="视频输出目录")
    ap.add_argument("--manifest", default="", help="已下载记录路径（默认 data/state/tg_manifest.json）")
    ap.add_argument("--primary", default="", help="主文件名用的番号；默认取 --alias/清单命中的第一个")
    ap.add_argument("--alias", default="", help="逗号分隔的其余番号，为它们建硬链接（同一文件多个名字）")
    ap.add_argument("--parts", type=int, default=2, help="分段并发数（默认 2）")
    ap.add_argument("--stall", type=int, default=180, help="连续多少秒零字节判卡死（默认 180）")
    ap.add_argument("--proxy", default="127.0.0.1:7890:http")
    ap.add_argument("--overwrite", action="store_true", help="已存在同名文件也重下")
    ap.add_argument("--dry-run", action="store_true", help="只看这条消息是什么，不下")
    return ap.parse_args()


def codes_in_caption(caption):
    """正文里出现的所有番号（宽松抽，只用于展示与选主文件名；下载侧不做校验）。

    注意这里的正则会连「π1672」这类无关数字一起抽出来，所以只当参考，
    真正要下哪些番号一律由 --alias 显式指定。
    """
    txt = tg_dl.LINK_RX.sub(" ", caption or "")
    out = []
    for m in re.finditer(r"([A-Z]{2,8})[-_ ]?(\d{2,6})", txt.upper()):
        code = "%s-%s" % (m.group(1), m.group(2))
        if code not in out:
            out.append(code)
    return out


def link_or_copy(src, dst):
    """给同一份视频再起一个名字：优先硬链接（不占空间），失败回退复制。"""
    if Path(dst).exists():
        return "已存在"
    try:
        os.link(src, dst)
        return "硬链接"
    except OSError as e:
        shutil.copy2(src, dst)
        return "复制(%.1f MB，硬链接失败:%s)" % (os.path.getsize(src) / MB, type(e).__name__)


async def run():
    a = parse_args()
    ids = [int(x) for x in str(a.ids).split(",") if x.strip()]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    man_path = tg_dl.manifest_path(out, a.manifest)
    man = tg_dl.load_manifest(man_path) if Path(man_path).exists() else {}

    args = SimpleNamespace(api_id=None, api_hash=None, proxy=a.proxy, session="tgdl")
    client = tg_dl.make_client(args, "tgdl")
    await client.connect()
    entity = await tg_dl.get_entity(client, a.target)
    print("已连上 %s (id=%s) | 记录 %s（%d 条）" % (entity.title, entity.id, man_path, len(man)))

    for mid in ids:
        msg = await client.get_messages(entity, ids=mid)
        if not msg:
            print("msg %s：取不到（已删除？）" % mid)
            continue
        kind = tg_dl.media_kind(msg)
        caps = codes_in_caption(msg.text)
        print("\nmsg %s | %s | %.1f MB | 正文番号: %s"
              % (mid, kind[0] if kind else "无媒体", (kind[1] / MB if kind else 0), " ".join(caps)))
        if not kind:
            print("  这条没有可下的媒体，跳过")
            continue
        if a.dry_run:
            print("  [--dry-run] 不下")
            continue

        aliases = [x.strip().upper() for x in str(a.alias).split(",") if x.strip()]
        primary = (a.primary or (aliases[0] if aliases else caps[0] if caps else "msg%s" % mid)).upper()
        ext = Path(kind[3] or "video.mp4").suffix or ".mp4"
        dest = out / ("%s%s" % (primary, ext))
        key = "%d_%d" % (entity.id, mid)

        if dest.exists() and not a.overwrite:
            sz = dest.stat().st_size
            print("  主文件已存在（%.1f MB），不覆盖" % (sz / MB))
        else:
            item = {"key": key, "kind": kind, "msg": msg, "size": kind[1],
                    "name": dest.name}
            tmp = dest.with_name(dest.name + ".part")
            prog = {"done": 0}
            ok = False
            for attempt in range(3):
                prog["done"] = 0
                try:
                    await tg_dl.download_slices(client, item, tmp, prog, a)
                    ok = True
                    break
                except asyncio.CancelledError:
                    print("  卡死 %ds 无字节进账，刷新 file_reference 重试" % a.stall, flush=True)
                    fresh = await client.get_messages(entity, ids=mid)
                    if fresh:
                        item["msg"] = fresh
                except Exception as e:
                    print("  第%d次失败 %s: %s" % (attempt + 1, type(e).__name__, str(e)[:90]), flush=True)
                    fresh = await client.get_messages(entity, ids=mid)   # 老消息必做：file_reference 会过期
                    if fresh:
                        item["msg"] = fresh
                await asyncio.sleep(3)
            if not ok:
                print("  三次都失败，放弃这条")
                continue
            os.replace(tmp, dest)
            print("  完成 %s (%.1f MB)" % (dest.name, dest.stat().st_size / MB))

        man[key] = {"name": dest.name, "size": dest.stat().st_size,
                    "msg": mid, "date": str(msg.date), "mix": sorted(aliases or caps)}
        tg_dl.save_manifest(man_path, man)

        rest = [x for x in aliases if x != primary] or [x for x in caps if x != primary]
        for c in rest:
            d2 = out / ("%s%s" % (c, ext))
            print("    %-10s -> %s" % (c, link_or_copy(dest, d2)))

    await client.disconnect()
    print("\nmanifest 现有 %d 条记录" % len(man))


if __name__ == "__main__":
    asyncio.run(run())
