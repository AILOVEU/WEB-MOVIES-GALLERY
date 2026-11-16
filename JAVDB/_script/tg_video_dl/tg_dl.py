#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""无界面（纯终端）下载 Telegram 群里的视频到本地目录。

依赖：telethon + python-socks（已装在 Tools/tg_video_dl/.venv）
凭据：TG_API_ID / TG_API_HASH 环境变量，或同目录 credentials.txt（api_id=xxx / api_hash=xxx）

用法示例（在仓库根目录执行）：
  P=Tools/tg_video_dl/.venv/Scripts/python.exe
  $P Tools/tg_video_dl/tg_dl.py --check                     # 只测 MTProto 能否经代理连通
  $P Tools/tg_video_dl/tg_dl.py --login                     # 终端输手机号+验证码，生成 session
  $P Tools/tg_video_dl/tg_dl.py --target Sgp987 --list      # 只列视频清单与体积
  $P Tools/tg_video_dl/tg_dl.py --target Sgp987 --latest 20 # 下载最近 20 个带编号视频（目录见 --out）
  $P Tools/tg_video_dl/tg_dl.py --all                       # 连没有编号的视频也下
"""
# [own] 分类=download 自写脚本（tg_video_dl 下载器，原属 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁出仓库），非 Telegram 上游
import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from telethon import TelegramClient, errors, functions, types  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402

HERE = Path(__file__).resolve().parent
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".3gp", ".ts"}
MB = 1024 * 1024
# 番号只认「番号：」标记后的代码，避免从广告链接里认错（t.me/qsty8999 → QSTY-8999）
MARK_RX = re.compile(r"番号\s*[：:]?\s*[`'\"“”‘’\[【(]*\s*([A-Za-z]{2,6})\s*[-_ ]?\s*(\d{3,5})")
# Markdown 链接 / 裸 URL 先剔除
LINK_RX = re.compile(r"\[[^\]]*\]\([^)]*\)|https?://\S+|t\.me/\S+")
# 文件名里的番号形态：ABC-123 / ABC123
CODE_RES = [
    re.compile(r"([A-Za-z]{2,6})[-_ ](\d{3,5})"),
    re.compile(r"([A-Za-z]{2,6})(\d{3,5})"),
]
# 手机默认名 / 流水号（IMG_7939、3、2028）不能当番号用
JUNK_STEM_RX = re.compile(r"(?i)^(?:IMG|VID|MOV|CLIP|SCREENRECORD)?[-_]?\d+$")


OUT_DEFAULT = r"D:\workspace\z_WEB-MOVIES-GALLERY\JAVDB\assets\水果派"
# 已下载记录的新家（与仓库其它清单同放 data/state）；HERE = _script/tg_video_dl
STATE_DEFAULT = HERE.parent.parent / "data" / "state" / "tg_manifest.json"
LEGACY_MANIFEST = "_tg_manifest.json"


def load_creds(args):
    api_id = args.api_id or os.environ.get("TG_API_ID")
    api_hash = args.api_hash or os.environ.get("TG_API_HASH")
    cred = HERE / "credentials.txt"
    if (not api_id or not api_hash) and cred.exists():
        kv = dict(re.findall(r"(api_id|api_hash)\s*=\s*(\S+)", cred.read_text(encoding="utf-8")))
        api_id = api_id or kv.get("api_id")
        api_hash = api_hash or kv.get("api_hash")
    if not api_id or not api_hash:
        sys.exit("缺少 api_id/api_hash：设置 TG_API_ID/TG_API_HASH，或写 %s（my.telegram.org 申请）" % cred)
    return int(api_id), api_hash.strip()


def parse_proxy(spec):
    """127.0.0.1:7890 或 127.0.0.1:7890:http / socks5；返回 Telethon 需要的 dict"""
    if not spec or spec == "none":
        return None
    parts = spec.split(":")
    if len(parts) == 2:
        parts.append("http")
    host, port, kind = parts[0], int(parts[1]), parts[2]
    return {"proxy_type": kind, "addr": host, "port": port}


def make_client(args, session_name="tgdl", session_obj=None, timeout=None):
    api_id, api_hash = load_creds(args)
    if session_obj is not None:
        sess = session_obj
    else:
        session = HERE / "session" / session_name
        session.parent.mkdir(parents=True, exist_ok=True)
        sess = str(session)
    return TelegramClient(sess, api_id, api_hash,
                          proxy=parse_proxy(args.proxy),
                          device_model="pc-cli", system_version="windows",
                          app_version="1.0", request_retries=4, connection_retries=4,
                          timeout=timeout or getattr(args, "timeout", 30),
                          auto_reconnect=True)


def export_string_session(client):
    """把已登录的 session 导出成 StringSession 字符串，供连接池其它客户端共用授权。

    为什么不用同一个 session 文件：SQLiteSession 被多个客户端并发写会锁库，
    且各自覆盖 update state。StringSession 是纯内存的，克隆出来互不干扰。
    """
    s = client.session
    if isinstance(s, StringSession):
        return s.save()
    ss = StringSession()
    ss.set_dc(s.dc_id, s.server_address, s.port)
    ss.auth_key = s.auth_key
    return ss.save()


async def build_pool(client, args, n):
    """在已登录的主客户端之外再建 n-1 个共用授权的客户端，组成真连接池。

    这是提速的关键。Telethon 的 iter_download 在单条连接上严格串行
    （downloads.py 的 _load_next_chunk 一次只发一个 GetFileRequest，等回复才发下一个），
    所以单连接上堆并发只是队头阻塞 —— 这就是旧版 --parts 越多越慢的真正原因。
    要提速必须有多条独立的 TCP 连接。
    """
    n = max(1, int(n or 1))
    if n == 1:
        return [client]
    auth = export_string_session(client)
    pool = [client]
    for i in range(1, n):
        c = make_client(args, session_obj=StringSession(auth))
        c._receive_updates = False          # 池里的副连接不收更新，省带宽也避免重复处理
        await c.connect()
        if not await c.is_user_authorized():
            await c.disconnect()
            print("  连接池第 %d 条连接授权失败，池降级为 %d 条" % (i + 1, len(pool)))
            break
        pool.append(c)
    print("连接池就绪：%d 条独立连接（每条各自一条 TCP，分段并行才真正生效）" % len(pool))
    return pool


async def close_pool(pool):
    for c in pool:
        try:
            await c.disconnect()
        except Exception:
            pass



def resolve_target(target):
    """返回 (username|None, invite_hash|None)"""
    t = target.strip().rstrip("/")
    if t.startswith("+"):
        return None, t[1:]
    m = re.search(r"t\.me/(?:s/)?([^/?#]+)", t)
    name = m.group(1) if m else t.lstrip("@")
    if name.startswith("+"):
        return None, name[1:]
    return name, None


async def get_entity(client, target):
    uname, invite = resolve_target(target)
    if invite:
        try:
            found = await client(functions.messages.CheckChatInviteRequest(hash=invite))
            if isinstance(found, types.ChatInvite):  # 尚未加入
                found = await client(functions.messages.ImportChatInviteRequest(hash=invite))
            return found.chats[0] if getattr(found, "chats", None) else found.chat
        except errors.rpcerrorlist.UserAlreadyParticipantError:
            print("已在群内，改按用户名解析")
        except Exception as e:
            print("通过邀请链接加入失败：%s %s" % (type(e).__name__, str(e)[:120]))
    if not uname:
        sys.exit("无法解析目标，--target 可以是 用户名 / t.me 链接 / +邀请链接")
    try:
        return await client.get_entity(uname)
    except (errors.UsernameNotOccupiedError, ValueError, TypeError) as e:
        print("按 username 解析失败：%s，尝试加入公有群" % str(e)[:100])
        ent = await client.get_entity(uname)
        await client(functions.channels.JoinChannelRequest(channel=ent))
        return await client.get_entity(uname)


def media_kind(msg):
    """命中视频返回 (类别, 字节数, 扩展名, 文档原名)，否则 None；类别 in {video, round, doc}

    Telethon 新版 layer 里视频（含圆形短视频）统一以 MessageMediaDocument + DocumentAttributeVideo 下发。
    """
    m = msg.media
    if m is None:
        return None
    video_media = getattr(types, "MessageMediaVideo", None)      # 兼容旧版本
    round_media = getattr(types, "MessageMediaRoundVideo", None)
    if video_media is not None and isinstance(m, video_media) and m.video:
        ext = "." + ((m.video.mime_type or "video/mp4").split("/")[-1] or "mp4")
        return "video", m.video.size or 0, ext, ""
    if round_media is not None and isinstance(m, round_media) and m.video:
        return "round", m.video.size or 0, ".mp4", ""
    if not isinstance(m, types.MessageMediaDocument) or not m.document:
        return None
    d = m.document
    attrs = d.attributes or []
    vattr = next((a for a in attrs if isinstance(a, types.DocumentAttributeVideo)), None)
    fname = next((a.file_name for a in attrs if isinstance(a, types.DocumentAttributeFilename)), "")
    mime = d.mime_type or ""
    ext = Path(fname).suffix.lower()
    if vattr is None and not mime.startswith("video/") and ext not in VIDEO_EXT:
        return None
    if vattr is not None and getattr(vattr, "round_message", False):
        kind = "round"
    elif mime.startswith("video/") or vattr is not None:
        kind = "video"
    else:
        kind = "doc"
    return kind, d.size or 0, ext or (".mp4" if kind != "doc" else ".bin"), fname


def norm(m):
    return "%s-%s" % (m.group(1).upper(), m.group(2))


def find_code(caption):
    """只取正文「番号：」标记后的番号，归一化成 ABC-123；没有则返回 None"""
    m = MARK_RX.search(LINK_RX.sub(" ", caption or ""))
    return norm(m) if m else None


def code_from_name(fname):
    """文档原名里的番号（兵底）；IMG_7939 / 3.mp4 这类默认名直接戒掉"""
    stem = Path(fname).stem if fname else ""
    if not stem or JUNK_STEM_RX.match(stem):
        return None
    for rx in CODE_RES:
        m = rx.search(stem)
        if m:
            return norm(m)
    return None


def compact(text):
    """归一化：大写 + 去掉一切非字母数字，让 SSIS-338 / ssis338 / SSIS 338 互相能对上"""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


def load_watched(path, min_rating=4.0):
    """解析 watched.js（window.JAVDB_WATCHED = {"CODE": 4.0, ...}）

    返回 {压缩番号: 原始大写番号}，只保留评分 >= min_rating 的条目。
    """
    p = Path(path)
    if not p.exists():
        sys.exit("关注表不存在：%s" % p)
    pairs = re.findall(r'"([^"]+)"\s*:\s*([0-9.]+)', p.read_text(encoding="utf-8", errors="replace"))
    wanted = {}
    for code, rating in pairs:
        if float(rating) >= min_rating:
            key = compact(code)
            if key:
                wanted[key] = code.strip().upper()
    if not wanted:
        sys.exit("关注表里没有评分 >= %s 的条目" % min_rating)
    return wanted


def load_codes(source):
    """从目录 / JSON / 逐行文本收集番号清单，返回 {压缩番号: 大写原始番号}

    目录取视频文件主名；JSON 取 items[].code；只保留含数字的番号，因为纯工作室名
    （Blacked/Tushy/Vixen）无编号，子串匹配极易误命中广告文案。
    """
    p = Path(source)
    codes = []
    if p.is_dir():
        codes = [f.stem for f in sorted(p.iterdir()) if f.suffix.lower() in VIDEO_EXT]
    elif p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        items = data.get("items", data) if isinstance(data, dict) else data
        if isinstance(items, dict):
            codes = [v.get("code") or k for k, v in items.items() if isinstance(v, dict)]
        else:
            codes = list(items or [])
    else:
        codes = [l.strip() for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    wanted = {}
    for c in codes:
        key = compact(c)
        if key and any(ch.isdigit() for ch in key):
            wanted[key] = c.strip().upper()
    if not wanted:
        sys.exit("清单里没抽到可用番号：%s" % p)
    return wanted


def match_watched(caption, fname, wanted):
    """正文/原名里命中关注表番号则返回那个番号，否则 None

    先按「番号：」标记精确抽，抽不到再把全文压缩后逐个关注号试命中。
    """
    m = MARK_RX.search(LINK_RX.sub(" ", caption or ""))
    if m:
        key = compact("%s%s" % (m.group(1), m.group(2)))
        if key in wanted:
            return wanted[key]
    hay = compact(caption) + " " + compact(fname)
    for key, orig in wanted.items():
        if len(key) >= 5 and key in hay:
            return orig
    return None


def safe_name(text, limit=48):
    """清洗成安全文件名，保留中文、字母、数字和下划线"""
    return re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", text or "").strip("._")[:limit] or "video"


def build_name(fname, caption, msg_id, ext, require_num, seen):
    """返回最终文件名（就是 ABC-123.mp4）；None 表示无番号、按规则跳过

    优先级：正文「番号：」标记 > 文档原名里的番号。
    """
    code = find_code(caption) or code_from_name(fname)
    if require_num and not code:
        return None
    name = "%s%s" % (code or "msg%06d" % msg_id, ext)
    if name in seen:                      # 同番号重复发时不互相覆盖
        name = "%s_%d%s" % (code, msg_id, ext)
    seen.add(name)
    return name


def manifest_path(out, manifest=""):
    """已下载记录该读写哪儿：`--manifest` 指定 > `data/state/tg_manifest.json` 新默认 > 资源目录里的老位置。

    为什么从 `assets/水果派/_tg_manifest.json` 挑到 `data/state/`：资源目录会被整体搬去别的硬盘，
    记录留在里面就有「搬一次目录丢一份档案」的风险（2026-11-04 实测：talk 82 条的文件全不在场，
    只剩这份 manifest 侥幸留下）；`videos_manifest.json` / `covers_manifest.json` 也都在 `data/state/`。
    老位置若还存在就先读它，下一次 save 写进新默认 —— 等于自动搬家，不丢历史。"""
    if manifest:
        return Path(manifest)
    if STATE_DEFAULT.exists():
        return STATE_DEFAULT
    legacy = Path(out) / LEGACY_MANIFEST
    return legacy if legacy.exists() else STATE_DEFAULT


def dump_codes(items, entity, args, path):
    """把本次扫到的全量番号落盘（缓存）。

    为什么要缓存：全量扫一遍群历史消息特别贵（几千条分页），而后续对账（跟 series.js /
    已下清单比）只需要番号+大小+日期。缓存一份就能离线反复算，不必每次重连 TG 重扫。"""
    data = {"updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "target": args.target, "entity_id": getattr(entity, "id", None),
            "count": len(items), "bytes": sum(i["size"] for i in items),
            "items": [{"code": Path(i["name"]).stem, "name": i["name"], "kind": i["kind"],
                       "msg": i["msg"].id, "size": i["size"],
                       "date": str(i["msg"].date)[:10]} for i in items]}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print("全量番号缓存已写入 %s（%d 个番号，合计 %.2f GB）"
          % (p, data["count"], data["bytes"] / 1073741824), flush=True)


def load_manifest(path):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_manifest(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


async def collect(client, entity, args):
    items, scanned, skipped = [], 0, 0
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    seen = set()
    taken = set()                # 清单模式下已收录的番号（同番号重发只取最新一个）
    wanted = load_watched(args.watched, args.min_rating) if args.watched else None
    if args.codes:
        wanted = load_codes(args.codes)
        print("番号清单已加载：%d 个（来源 %s，不看评分）" % (len(wanted), args.codes))
    elif wanted:
        print("关注表已加载：%d 个番号（评分 >= %s）" % (len(wanted), args.min_rating))
    async for msg in client.iter_messages(entity, limit=(args.scan or None)):
        scanned += 1
        picked = media_kind(msg)
        if not picked or picked[0] not in kinds:
            continue
        kind, size, ext, fname = picked
        if wanted:
            code = match_watched(msg.text, fname, wanted)
            if not code:
                skipped += 1
                continue
            if code in taken:     # 同一个片子被反复发布，不重复下载
                skipped += 1
                continue
            taken.add(code)
            name = "%s%s" % (code, ext)
            if name in seen:
                name = "%s_%d%s" % (code, msg.id, ext)
            seen.add(name)
        else:
            name = build_name(fname, msg.text, msg.id, ext, not args.all, seen)
            if name is None:
                skipped += 1
                continue
        items.append({"key": "%d_%d" % (entity.id, msg.id), "kind": kind,
                      "msg": msg, "size": size, "name": name})
        if args.latest and len(items) >= args.latest:
            break
    print("扫描 %d 条，命中并入选 %d 个（合计 %.1f MB），不符规则跳过 %d 个"
          % (scanned, len(items), sum(i["size"] for i in items) / MB, skipped))
    return items


PART = 512 * 1024   # 分片边界对齐单位（Telethon 单请求上限 512 KiB）


def slices_for(size, n):
    """把文件切成 n 段，段边界对齐 512 KiB；返回 [(偏移, 长度), ...]"""
    if size <= 0:
        return []
    n = max(1, min(n, (size + PART - 1) // PART))
    step = ((size + n - 1) // n + PART - 1) // PART * PART
    out, off = [], 0
    while off < size:
        lim = min(step, size - off)
        out.append((off, lim))
        off += lim
    return out


def _raw_iter(client, media, off, limit, chunk, size, dc_id, msg_data):
    """优先用带 msg_data 的迭代器，让 Telethon 自己刷新过期的 file_reference。

    downloads.py:118-139 有完整的自愈逻辑（重新 get_messages 取新 file_reference 再续），
    但只在 msg_data 传进去时才生效；公开的 iter_download 没有这个参数，只有 _iter_download 有。
    拿不到就退回公开 API，行为与旧版一致。
    """
    if msg_data and hasattr(client, "_iter_download"):
        try:
            return client._iter_download(media, offset=off, limit=limit, chunk_size=chunk,
                                         request_size=chunk, file_size=size, dc_id=dc_id,
                                         msg_data=msg_data)
        except TypeError:
            pass
    return client.iter_download(media, offset=off, limit=limit, chunk_size=chunk,
                                request_size=chunk, file_size=size, dc_id=dc_id)


async def _one_slice(client, media, dc_id, size, off, lim, path, prog, chunk, msg_data=None):
    """拉一个分段；已有部分则从断点接着拉（字节级续传，取消/异常绝不删已下数据）

    iter_download 每次产出一个 request_size 响应，所以已写字节数天然对齐。
    """
    have = path.stat().st_size if path.exists() else 0
    if have > lim:                        # 比目标还大只能是脏数据
        path.unlink()
        have = 0
    prog["done"] += have
    if have == lim:
        return lim
    got = have
    # 注意：iter_download 的 limit 是「最多产出多少个 chunk」而不是字节数！
    # 传字节数会被当成天文数字个分片 → 一路写到文件末尾（分段超界、合片损坏）。
    left = lim - have
    with open(path, "ab") as fd:
        async for chunk_data in _raw_iter(client, media, off + have,
                                          (left + chunk - 1) // chunk, chunk, size, dc_id, msg_data):
            if got + len(chunk_data) > lim:          # 双重保险：绝不越段界一个字节
                chunk_data = chunk_data[:lim - got]
            fd.write(chunk_data)                     # 不再逐片 flush：交给 OS 缓冲，省掉每 512 KiB 一次系统调用
            got += len(chunk_data)
            prog["done"] += len(chunk_data)
            if got >= lim:
                break
    if got != lim:
        raise RuntimeError("分段 off=%d 收到 %d 期望 %d" % (off, got, lim))
    return lim


async def download_slices(client, item, tmp, prog, args):
    """并行分段下载 + 卡死看门狗

    client 可以是单个客户端，也可以是连接池（列表）。池里每条是独立 TCP 连接，
    分段派发到不同连接才能真正并行 —— 单连接上堆并发只会互相排队。
    连续 stall 秒零字节进账则取消重试。
    """
    pool = client if isinstance(client, (list, tuple)) else [client]
    media = item["msg"].media
    size = item["size"]
    dc_id = getattr(getattr(media, "document", None), "dc_id", None)
    chunk = int(getattr(args, "chunk", 0) or PART)
    parts = slices_for(size, getattr(args, "parts", 1))
    tmps = [tmp.with_name("%s.p%d" % (tmp.name, i)) for i in range(len(parts))]
    entity = item.get("entity")
    msg_data = (entity, item["msg"].id) if entity is not None else None

    task = asyncio.ensure_future(asyncio.gather(*[
        _one_slice(pool[i % len(pool)], media, dc_id, size, o, l, p, prog, chunk, msg_data)
        for i, ((o, l), p) in enumerate(zip(parts, tmps))]))
    seen, stuck, shown = prog["done"], 0, 0
    while not task.done():
        await asyncio.sleep(5)
        if prog["done"] != seen:
            seen, stuck = prog["done"], 0
            pct = int(seen * 100 / size) if size else 0
            if pct - shown >= 20:
                shown = pct
                print("  %s ... %d%%" % (item["name"][:40], pct), flush=True)
        else:
            stuck += 5
            if stuck >= getattr(args, "stall", 120):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise asyncio.CancelledError()
    task.result()

    with open(tmp, "wb") as fd:                     # 按序合片
        for p in tmps:
            with open(p, "rb") as s:
                while True:
                    buf = s.read(4 * 1024 * 1024)
                    if not buf:
                        break
                    fd.write(buf)
            p.unlink()


async def guarded_download(client, item, tmp, cb, prog, stall):
    """带卡死看门狗的下载：连续 stall 秒零字节进账就取消，避免死隧道把作业挂死数小时

    prog 是 download_one 里的共享进度字典，cb 写、这里读。
    """
    task = asyncio.ensure_future(
        client.download_media(item["msg"].media, file=str(tmp), progress_callback=cb))
    seen, stuck = prog["done"], 0
    while not task.done():
        await asyncio.sleep(5)
        if prog["done"] != seen:
            seen, stuck = prog["done"], 0
        else:
            stuck += 5
            if stuck >= stall:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise asyncio.CancelledError()
    return task.result()


async def download_one(client, entity, item, out, man, sem, args):
    async with sem:
        dest = Path(out) / item["name"]
        if item["key"] in man and dest.exists() and dest.stat().st_size == man[item["key"]]["size"]:
            print("跳过已下载 %s" % item["name"])
            return "skip"
        if dest.exists() and not args.overwrite:
            # 磁盘上已有同名文件但 manifest 没记（手工挪过盘、换过目录等）：
            # 绝不覆盖已下载的视频，登记后跳过；要强制重下才用 --overwrite
            sz = dest.stat().st_size
            man[item["key"]] = {"name": item["name"], "size": sz, "msg": item["msg"].id,
                                "date": str(item["msg"].date)}
            save_manifest(args.man_path, man)
            print("已存在同名文件，不覆盖，登记后跳过 %s (%.1f MB)" % (item["name"], sz / MB))
            return "skip"
        tmp = dest.with_name(dest.name + ".part")
        if tmp.exists() and tmp.stat().st_size > (item["size"] or 1):
            tmp.unlink()  # 残留比目标还大，说明脏了，重下
        last = [0]
        prog = {"done": 0}
        item["entity"] = entity          # 供 download_slices 传 msg_data，让 Telethon 自愈 file_reference

        def cb(done, total):
            prog["done"] = done
            if total:
                pct = int(done * 100 / total)
                if pct - last[0] >= 20:
                    last[0] = pct
                    print("  %s ... %d%%" % (item["name"][:40], pct), flush=True)

        ok = False
        for attempt in range(3):
            prog["done"] = 0      # 每次重试都归零：_one_slice 会把已有字节重新计入，否则进度会算到 100% 以上
            try:
                if item["size"] > 0:
                    # 单流也走分段路径：目的是拿到 append 模式的字节级续传
                    await download_slices(client, item, tmp, prog, args)
                else:
                    await guarded_download(client[0] if isinstance(client, (list, tuple)) else client,
                                           item, tmp, cb, prog, args.stall)
                ok = True
                break
            except asyncio.CancelledError:
                print("  %s 卡死 %ds 无字节迕移，已中断重试（已下分段会续用）"
                      % (item["name"][:32], args.stall), flush=True)
                fresh = await entity_client(client).get_messages(entity, ids=item["msg"].id)
                if fresh:
                    item["msg"] = fresh
                await asyncio.sleep(2)
            except (errors.FloodWaitError, errors.SlowModeWaitError) as e:
                print("  被限流，等待 %s 秒" % e.seconds, flush=True)
                await asyncio.sleep(e.seconds + 2)
            except Exception as e:
                print("  %s 第%d次失败 %s: %s"
                      % (item["name"][:32], attempt + 1, type(e).__name__, str(e)[:100]), flush=True)
                fresh = await entity_client(client).get_messages(entity, ids=item["msg"].id)  # 刷新 file_reference
                if fresh:
                    item["msg"] = fresh
                await asyncio.sleep(2)
        if not ok:
            return "fail"
        os.replace(tmp, dest)
        async with args.man_lock:        # 多 worker 并发下 manifest 是 read-modify-write，必须串行落盘
            man[item["key"]] = {"name": item["name"], "size": dest.stat().st_size,
                                "msg": item["msg"].id, "date": str(item["msg"].date)}
            save_manifest(args.man_path, man)
        print("完成 %s (%.1f MB)" % (item["name"], dest.stat().st_size / MB), flush=True)
        return "ok"


def entity_client(client):
    """池里取一条连接做元数据请求（get_messages 之类），固定用第一条避免分散。"""
    return client[0] if isinstance(client, (list, tuple)) else client



async def main():
    ap = argparse.ArgumentParser(description="无界面下载 Telegram 群视频")
    ap.add_argument("--target", default="Sgp987", help="群用户名 / t.me 链接 / +邀请链接")
    ap.add_argument("--out", default=OUT_DEFAULT, help="保存目录，默认为 %s" % OUT_DEFAULT)
    ap.add_argument("--proxy", default="127.0.0.1:7890:http", help="host:port[:http|socks5]，none=不走代理")
    ap.add_argument("--api-id", type=int)
    ap.add_argument("--api-hash")
    ap.add_argument("--session", default="tgdl")
    ap.add_argument("--latest", type=int, default=0, help="最多下载几个视频，0=不限")
    ap.add_argument("--scan", type=int, default=0, help="最多扫描多少条消息，0=全部")
    ap.add_argument("--kinds", default="video,round,doc", help="video|round|doc 逗号分隔")
    ap.add_argument("--watched", default=r"D:\workspace\z_WEB-MOVIES-GALLERY\JAVDB\data\site\watched.js",
                    help="关注表 watched.js 路径；传空字符串则不按关注表筛选")
    ap.add_argument("--min-rating", type=float, default=4.0, help="只下评分 >= 此值的番号")
    ap.add_argument("--codes", default="",
                    help="改用番号清单（目录或 videos_manifest.json 或逐行文本），命中就下、不看评分")
    ap.add_argument("--overwrite", action="store_true",
                    help="允许覆盖磁盘上已有的同名文件（默认绝不覆盖，避免删掉已下载的视频）")
    ap.add_argument("--all", action="store_true",
                    help="连没有番号/标题的视频也下（默认只下带编号的，如 video_1.mp4 / 第3集）")
    ap.add_argument("--workers", type=int, default=2, help="并发下载文件数，建议 2-3")
    ap.add_argument("--conns", type=int, default=6,
                    help="连接池大小（独立 TCP 连接数）。这是提速的关键：Telethon 单连接严格串行，"
                         "必须多连接才能真并行。2026-11-11 实测（交错 A/B，排除网络漂移）："
                         "1 连接 3.2 MB/s、2 连接 6.0、4 连接 11.2、6 连接 12.4（最佳）、"
                         "8 连接 11.9、12 连接 10.7。默认 6，网络差时可降到 2-4")
    ap.add_argument("--chunk", type=int, default=PART,
                    help="单次请求字节数，默认 %d（512 KiB，Telethon 硬上限）。"
                         "设成 1048576 会尝试 1 MiB 分片（tdl 的做法），但需实测服务端是否接受" % PART)
    ap.add_argument("--timeout", type=int, default=30,
                    help="单请求超时秒数，默认 30（Telethon 默认 10 走代理偏紧，是旧版 108 次超时的直接原因）")
    ap.add_argument("--stall", type=int, default=120,
                    help="连续多少秒零字节进账就判定卡死并重试（0=不启用看门狗）")
    ap.add_argument("--parts", type=int, default=6,
                    help="单文件切几段。段数应 >= 连接数才能喂满池子，默认 6（与 --conns 对齐）。"
                         "旧版默认 1 是因为单连接下堆并发只会互相排队；有连接池后必须切段")

    ap.add_argument("--manifest", default="",
                    help="已下载记录 json，默认 %s（若资源目录里还有老的 %s 会先读它再自动搬家）"
                         % (STATE_DEFAULT, LEGACY_MANIFEST))
    ap.add_argument("--list", action="store_true", help="只列清单不下载")
    ap.add_argument("--dump-codes", default="",
                    help="把本次扫到的全量番号写成 JSON 缓存（配合 --list 只扫不下），供离线对账")
    ap.add_argument("--check", action="store_true", help="只测试代理连通性")
    ap.add_argument("--login", action="store_true", help="终端交互登录并保存 session")
    args = ap.parse_args()

    client = make_client(args, args.session)
    await client.connect()
    print("MTProto 连通 OK（已连上 Telegram DC，代理 %s 可用）" % args.proxy)

    if not await client.is_user_authorized():
        if not args.login:
            sys.exit("该 session 未登录，请先执行：tg_dl.py --login")
        await client.start()  # 终端提示手机号 / 验证码 / 两步密码，全程无 GUI
        print("登录成功，session 已保存到 %s" % (HERE / "session" / args.session))
    print("授权状态：%s" % ("已登录" if await client.is_user_authorized() else "未登录"))
    if args.check or not args.target:
        await client.disconnect()
        return

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    args.man_path = manifest_path(out, args.manifest)
    print("已下载记录：%s" % args.man_path)
    entity = await get_entity(client, args.target)
    print("目标：%s (id=%s, 成员=%s)" % (getattr(entity, "title", "?"), entity.id,
                                        getattr(entity, "participants_count", "?")))

    items = await collect(client, entity, args)
    if args.dump_codes:
        dump_codes(items, entity, args, args.dump_codes)
    for i in items:
        print("  msg %-7s %8.1f MB  %s" % (i["msg"].id, i["size"] / MB, i["name"]))
    if args.list:
        await client.disconnect()
        return

    man = load_manifest(args.man_path)
    sem = asyncio.Semaphore(max(1, args.workers))
    args.man_lock = asyncio.Lock()
    pool = await build_pool(client, args, args.conns)
    t0 = time.time()
    try:
        res = await asyncio.gather(*[download_one(pool, entity, i, out, man, sem, args) for i in items])
    finally:
        await close_pool(pool)
    print("总计 成功%d 跳过%d 失败%d，用时 %.1f 分钟，目录 %s"
          % (res.count("ok"), res.count("skip"), res.count("fail"), (time.time() - t0) / 60, out))
    await client.disconnect()



if __name__ == "__main__":
    asyncio.run(main())
