# -*- coding: utf-8 -*-
"""
s04d_rewrite.py — 把 manifest 里的本地图片路径回写进年度容器
- images[].local 填本地相对路径
- content_html 里 <img src> 改写为本地相对路径（并清掉 data-src 等远程属性）
- comments[].avatar_local 填本地头像路径

读写对象是每篇一份的原料容器 `_pipeline/material/<年>/<key>.jsonl` 的 parsed 段（见 years.py）。
回写用 touch=False + 「内容相同不写盘」，所以每轮全树跑也只写真正变过的容器，
旧文件的 mtime 不动 —— 迁移工具（robocopy 按 mtime+size）因此只把真变化计为待传。

用法:
  python s04d_rewrite.py                      全树回写（历史行为，rebuild.py --all 用它兜底）
  python s04d_rewrite.py --keys-file=<路径>  只回写本轮增量范围那几篇

为什么要范围入口：旧文章的 content_html 在它自己那一轮已经回写过，manifest 台账又
不会凭空变 —— 每轮把它们全读一遍（七千篇 × 平均 254KB ≈ 1.8GB，实测 165 秒）
只为了得出一行「内容未变、零写入」。范围模式把这份代价降到十几篇，全树回归
交给 _verify（它仍是全树的）与 rebuild.py --all / update.py --force。
"""
import os, re, sys, glob, hashlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C
from lxml import html as LH

IMG_SRC_ATTRS = ("src", "data-src", "data-original", "data-fl-origin")
# 评论头像占位图（站点根相对路径，由 s04p_make_avatar_placeholder.py 生成）
AVATAR_PLACEHOLDER = "assets/avatar-placeholder.png"
# 正文图片占位图（未落地死链图统一替代，2026-10-27 用户确认放弃剩余死链）
IMG_PLACEHOLDER = "assets/img-placeholder.png"
mpath = os.path.join(C.SRC, "image_manifest.json")
# 图片库闸门：拔盘状态下「本地路径 stat 失败」不代表引用坏了，此时改写会把
# 占位图/降级地址固化进 content_html（13.8 万处），插入硬盘后再跑即可自愈。
C.guard_or_exit("s04d")
man = C.read_json(mpath, {}) or {}
C.log("[s04d] manifest 条目 %d" % len(man))
# 增量本机模式（mode=incremental）：历史图在别的电脑上，本机 stat 不到是常态，
# 已经指向 images/... 的引用一律不动（库接回来就自愈），绝不回退远程地址/固化占位图。
new_only = C.new_images_only()
if new_only:
    C.log("[s04d] 图片库模式=incremental：本机看不到的历史引用保持原样（图在外部库）")
# 本轮增量范围：只回写这批（过滤在 open() 之前，真不开另外七千个容器）；不给 = 全树
SCOPE = C.scope_read(C.scope_arg())
if SCOPE:
    C.log("[s04d] 范围模式：只回写本轮 %d 篇（其余未读、未写、mtime 不动）" % len(SCOPE))

n_art = 0
rw = miss = av_rw = av_miss = 0
broken_revert = 0   # 坏本地引用 → 回退成远程 URL 的处数
broken_unfix = 0    # 坏本地引用 → 连原始远程地址都找不回的处数
disk_fix = 0        # 磁盘兜底翻案（manifest 无条目但哈希文件在）的处数
away_keep = 0       # 增量本机模式下「文件在外部库、引用原样保留」的处数
_io0 = C.Y.io_counts()


def disk_hit(u):
    """按 URL 哈希查磁盘：images/<h2>/<sha1[:20]>.<ext> 存在且非空 → 返回根相对路径。"""
    if not u or not u.startswith("http"):
        return None
    h = hashlib.sha1(u.encode("utf-8")).hexdigest()[:20]
    for p in glob.glob(os.path.join(C.IMAGES_DIR, h[:2], h + ".*")):
        try:
            if os.path.getsize(p) > 0:
                return "images/%s/%s" % (h[:2], os.path.basename(p).replace("\\", "/"))
        except OSError:
            pass
    return None


def do_rewrite(h, localmap, byraw, by_local_old):
    """把一段 content_html 里的 <img> 回写为本地路径；返回新串（累加全局计数）。"""
    global rw, miss, broken_revert, broken_unfix, disk_fix, away_keep
    if not h:
        return h
    try:
        el = LH.fragment_fromstring(h, create_parent="div")
    except Exception:
        return h
    for im in el.xpath(".//img"):
        src = im.get("src") or im.get("data-src") or ""
        if src == IMG_PLACEHOLDER:
            # 幂等：已占位 → 但仍给磁盘兜底一次翻案机会（历史边缘：
            # manifest/local 均无条目但文件实际在），有则改回真实路径
            real0 = im.get("title") or ""
            if real0.startswith("http") and disk_hit(real0):
                im.set("src", disk_hit(real0))
                del im.attrib["title"]
                disk_fix += 1
            continue
        real = im.get("data-fl-origin") or src
        # 归一化防抖：台账键只允许是 images/<xx>/<hash>.ext（§8 第 1 条）。万一有人把
        # assets/images/… 写进了容器（2026-11-04 目录重构就踩过：图片库并进 assets/ 后
        # relpath 造出了带 assets/ 前缀的新键，下面第 94 行的幂等判定认不出它，
        # 于是 280 处好图引用被当未落地写成占位图），这里先归回键形式再往下走。
        if src.startswith(C.IMG_SITE_PREFIX):
            src = C.IMG_KEY_PREFIX + src[len(C.IMG_SITE_PREFIX):]
            im.set("src", src)
            real = (C.IMG_KEY_PREFIX + real[len(C.IMG_SITE_PREFIX):]
                    if real.startswith(C.IMG_SITE_PREFIX) else real)
        if real.startswith(C.IMG_SITE_PREFIX):
            real = C.IMG_KEY_PREFIX + real[len(C.IMG_SITE_PREFIX):]
        # 幂等：已是本地相对路径（上一轮回写结果）→ 校验目标文件真实有效才跳过。
        # 若指向 0 字节空壳或文件缺失，必须回退成远程 URL 重新映射，
        # 否则坏路径会被固化进 HTML，离线站出现破图。
        if src.startswith("images/") and not real.startswith("http"):
            fp = os.path.normpath(C.img_disk_path(src))
            try:
                if os.path.exists(fp) and os.path.getsize(fp) > 0:
                    continue
            except OSError:
                pass
            if new_only:
                # 历史图已移到别的电脑：这个引用是库接回来后能直接生效的凭证，
                # 不能当成「坏路径」去回退/占位（那会把 13.8 万处引用写坏）。
                away_keep += 1
                continue
            back = by_local_old.get(src)
            if not back:
                base = os.path.basename(src)
                for _l, _u in by_local_old.items():
                    if os.path.basename(_l) == base:
                        back = _u
                        break
            if back:
                im.set("src", back)
                real = back
                broken_revert += 1
            else:
                # 本地坏路径找不回远程 → 直接占位图（不再保留破图路径）
                im.set("src", IMG_PLACEHOLDER)
                broken_unfix += 1
                continue
        loc = localmap.get(real) or byraw.get(src) or byraw.get(src.replace("&amp;", "&"))
        if loc:
            im.set("src", loc)
            rw += 1
        elif disk_hit(real):
            # 磁盘兜底：manifest 无条目但哈希文件在（历史边缘）→ 直接用
            im.set("src", disk_hit(real))
            rw += 1
            disk_fix += 1
        else:
            # 未落地（真死链）→ 统一占位图；原远程地址留 title 供追溯
            im.set("src", IMG_PLACEHOLDER)
            if real.startswith("http"):
                im.set("title", real)
            miss += 1
    for k in IMG_SRC_ATTRS:
        if k != "src" and k in el.attrib:
            del el.attrib[k]
    out = LH.tostring(el, encoding="unicode")
    if out.startswith("<div"):
        out = re.sub(r"^<div[^>]*>", "", out)
        out = re.sub(r"</div>$", "", out)
    return out


for url, key, slot, a in C.Y.iter_parsed(keys=C.scope_keys(SCOPE) if SCOPE else None):
    # raw_src → 真实 URL 的反查表：
    # 站点正文里的 <img src> 常是 sogoucdn 代理包装地址，
    # 而 images[].url 存的是还原后的真实原图地址。
    # 首轮回写时 data-fl-origin 属性会被清掉，后续轮次只能靠 raw_src 反查。
    byraw = {}
    # 旧 local → 远程 URL 反查表：必须在用新 manifest 覆盖 images[].local 之前建好，
    # 否则 manifest 清理掉坏条目后，content_html 里的坏本地路径就再也找不回原始远程地址。
    by_local_old = {}
    localmap = {}
    for im in a.get("images", []):
        ol = im.get("local") or ""
        if ol:
            by_local_old[ol] = im.get("url") or im.get("raw_src") or ""
    for im in a.get("images", []):
        ent = man.get(im.get("url"), {}) or {}
        im["local"] = ent.get("local", "")
        if im["local"]:
            localmap[im["url"]] = im["local"]
            rs = im.get("raw_src")
            if rs:
                byraw[rs] = im["local"]
                byraw[rs.replace("&amp;", "&")] = im["local"]
    a["image_local_map"] = localmap

    newh = do_rewrite(a.get("content_html", ""), localmap, byraw, by_local_old)
    a["content_html"] = newh
    # 分页：逐页回写（第 1 页与顶层 content_html 同体，直接复用）。
    for pg in a.get("pages", []):
        for im in pg.get("images", []):
            ent = man.get(im.get("url"), {}) or {}
            im["local"] = ent.get("local", "")
        if pg.get("n") == 1:
            pg["content_html"] = newh
        else:
            pg["content_html"] = do_rewrite(pg.get("content_html", ""), localmap, byraw, by_local_old)

    for c in a.get("comments", []):
        if c.get("avatar"):
            # 按用户要求：评论头像不逐个本地化，统一使用一张默认占位图。
            # 原头像文件（59612 个 / 181.9 MB）已从磁盘移除，manifest 中的头像条目一并清除。
            c["avatar_local"] = AVATAR_PLACEHOLDER
            c["avatar_is_default"] = True
            av_rw += 1

    # 写回容器（years 内部比对：内容一致就不写，mtime 保持）
    _io_before = C.Y.io_counts()["written"]
    C.Y.write_parsed(url, a, slot=slot, touch=False)
    if C.Y.io_counts()["written"] > _io_before:
        n_art += 1
        if n_art % 1000 == 0:
            C.log("[s04d] 已回写 %d 篇" % n_art)

_io = C.Y.io_counts()
same_cnt = _io["unchanged"] - _io0["unchanged"]
C.log("[s04d] 完成%s：改写 %d 篇（另 %d 篇内容未变、零写入）| 正文图本地化 %d 处（含磁盘兜底 %d）"
      "/ 占位图 %d 处 | 历史引用原样保留 %d 处 | 头像本地化 %d 处 / 未命中 %d 处"
      % ("（范围模式）" if SCOPE else "（全树）",
         n_art, same_cnt, rw, disk_fix, miss, away_keep, av_rw, av_miss))
print("REWRITE_OK %d %d %d %d %d %d" % (n_art, rw, miss, av_rw, av_miss, disk_fix))
