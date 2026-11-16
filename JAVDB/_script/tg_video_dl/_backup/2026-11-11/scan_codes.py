# -*- coding: utf-8 -*-
"""全量扫描 TG 群历史消息，抽出「每条消息里出现的所有番号」并缓存。

为什么不用 tg_dl.py --list --dump-codes 的结果当"全量番号"：
  tg_dl 的抽取规则是为「一条消息存成一个文件」设计的 —— 只认正文「番号：」标记后的
  **第一个**番号（MARK_RX）。而群里大量帖子不是这个形态：
    · 老帖写成 `<ADN-499 >`（根本没有"番号："字样）；
    · 合集帖一条消息列 3~10 个番号（「番号：」后用反引号串好多个，如 FSDSS-437、SSNI-497），只取第一个就把后面的全丢了。
  2026-11-04 实测：manifest 里 15 个已下番号的消息明明还在群里，却没进 --list 缓存，就是这两类。
  所以「群里到底有哪些番号」必须用宽松抽取 + 白名单校验，而不是复用下载命名规则。

做法：
  1. 遍历全部消息（不下载），筛出带视频的；
  2. 正文先剔除 Markdown/裸链接（防 t.me/qsty8999 之类广告号被认成番号），
     再用宽松 CAND_RX 抽出所有「字母2-8 + 数字3-7」候选；
  3. 候选必须命中白名单（movies.js 里的真实番号）才收录 —— 精度由白名单兜住，抽取才敢放宽；
  4. 一条消息里 N 个番号就各记一条，size 保留消息原体积、另存 share = size/N 便于平摊估算。

用法（在 tg_video_dl 目录下，必须用它的 venv，因为要 telethon）：
  .venv/Scripts/python.exe scan_codes.py                      # 全量扫，写缓存
  .venv/Scripts/python.exe scan_codes.py --scan 500           # 只扫最近 500 条（调试）
  .venv/Scripts/python.exe scan_codes.py --out ../../_pipeline/tg_videos_all.json
之后用 `python _script/manifest/tg_series_gap.py` 离线对账。
"""
import argparse
import asyncio
import io
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent                      # 仓库根（_script/tg_video_dl -> 仓库根）
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tg_dl                                   # noqa: E402  复用 client/entity/代理/凭据与 LINK_RX

# 宽松候选：字母 2-8 + 可选分隔 + 数字 3-7；前后不能再贴字母数字（避开流水号、日期串）
CAND_RX = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2,8})[-_ ]?\s?(\d{3,7})(?![A-Za-z0-9])")
# 明确标注的番号（优先级最高，直接收进候选池，仍要过白名单）
MARK_RX = re.compile(r"番号\s*[：:]?\s*[`'\"“”‘’\[【(]*\s*([A-Za-z]{2,8})[-_ ]?\s?(\d{3,7})")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".3gp", ".ts"}


def compact(text):
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def load_whitelist(path):
    """movies.js 的真实番号集合：{压缩键: 展示用原始大写番号}。"""
    if not os.path.isfile(path):
        sys.exit("找不到白名单 %s" % path)
    t = io.open(path, encoding="utf-8").read()
    var = "window.JAVDB_MOVIES"
    if var not in t:
        sys.exit("%s 里找不到 %s" % (path, var))
    i = t.index("=", t.index(var)) + 1
    d, _ = json.JSONDecoder().raw_decode(t[i:].lstrip())
    return {compact(c): c.strip().upper() for c in d.keys() if compact(c)}


def codes_in(text, fname, wantlist):
    """一条消息里所有能在白名单里对上的番号，返回 {键: (展示名, 是否「番号：」明确标注)}。"""
    hay = tg_dl.LINK_RX.sub(" ", text or "") + " " + (fname or "")
    hits = {}
    for m in MARK_RX.finditer(hay):
        key = compact("%s%s" % (m.group(1), m.group(2)))
        if key in wantlist:
            hits[key] = (wantlist[key], True)
    for m in CAND_RX.finditer(hay):
        key = compact("%s%s" % (m.group(1), m.group(2)))
        if key in wantlist and key not in hits:
            hits[key] = (wantlist[key], False)
    return hits


async def run(args):
    wantlist = load_whitelist(args.movies)
    print("白名单已加载：%d 个真实番号（来自 %s）" % (len(wantlist), args.movies))

    a = argparse.Namespace(api_id=None, api_hash=None, proxy=args.proxy, session=args.session)
    client = tg_dl.make_client(a, args.session)
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("session 未登录，先跑 tg_dl.py --login")
    entity = await tg_dl.get_entity(client, args.target)
    print("目标：%s (id=%s)" % (getattr(entity, "title", "?"), entity.id))

    items, scanned, vmsgs, nohit = [], 0, 0, 0
    async for msg in client.iter_messages(entity, limit=(args.scan or None)):
        scanned += 1
        picked = tg_dl.media_kind(msg)
        if not picked or picked[0] not in ("video", "round", "doc"):
            continue
        vmsgs += 1
        kind, size, ext, fname = picked
        hits = codes_in(msg.text, fname, wantlist)
        if not hits:
            nohit += 1
            continue
        n = len(hits)
        for key, (disp, marked) in hits.items():
            items.append({"code": disp, "key": key, "name": "%s%s" % (disp, ext or ".mp4"),
                          "kind": kind, "msg": msg.id, "size": size,
                          "share": round(size / n), "msg_codes": n, "marked": marked,
                          "date": str(msg.date)[:10]})
        if scanned % 500 == 0:
            print("  已扫 %d 条，视频 %d 条，收录 %d 条番号记录" % (scanned, vmsgs, len(items)), flush=True)

    data = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"), "target": args.target,
            "entity_id": entity.id, "scanned": scanned, "video_msgs": vmsgs,
            "no_code_msgs": nohit, "count": len(items),
            "codes": len({i["key"] for i in items}),
            "bytes_raw": sum(i["size"] for i in items),
            "bytes_shared": sum(i["share"] for i in items),
            "items": items}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    multi = sum(1 for i in items if i["msg_codes"] > 1)
    print("扫描 %d 条消息 | 带视频 %d 条（其中抽不到番号 %d 条）" % (scanned, vmsgs, nohit))
    print("收录番号记录 %d 条 | 去重番号 %d 个 | 来自合集帖的记录 %d 条" % (len(items), data["codes"], multi))
    print("体积：按消息原始计 %.1f GB / 按合集平摊计 %.1f GB"
          % (data["bytes_raw"] / 1073741824, data["bytes_shared"] / 1073741824))
    print("缓存 -> %s" % out)
    await client.disconnect()


def main():
    ap = argparse.ArgumentParser(description="全量抽取 TG 群消息里的番号（白名单校验）并缓存")
    ap.add_argument("--target", default="Sgp987")
    ap.add_argument("--proxy", default="127.0.0.1:7890:http")
    ap.add_argument("--session", default="tgdl")
    ap.add_argument("--scan", type=int, default=0, help="最多扫多少条消息，0=全部")
    ap.add_argument("--movies", default=str(ROOT / "data" / "site" / "movies.js"), help="番号白名单来源")
    ap.add_argument("--out", default=str(ROOT / "_pipeline" / "tg_videos_all.json"))
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
