# -*- coding: utf-8 -*-
"""
s04f_rebuild_manifest.py — 从磁盘实际文件重建 manifest
背景：s04b 分片被中断时 part 文件只在批次末尾写一次，导致「已下载但未入账」。
本脚本按「URL → sha1[:20] → images/xx/<hash>.*」反查磁盘，
把已存在的文件全部补进 manifest，并清理指向缺失文件的失效条目。

用法: python s04f_rebuild_manifest.py [--keys-file=<本轮范围>] [--allow-prune]

范围模式（update.py 增量轮用）：只从本轮那几篇收 URL，不再读全树 parsed（实测 33 秒
里 ~30 秒花在那七千次 open）；下面的「失效/重指/清理」那一步仍然扫全 manifest（只 stat
不读容器），因为那部分是「台账对不对」的全局判定，不能因为范围小就少查。
"""
import os, sys, glob, hashlib, collections

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C
import s04_fetch_images as S

# 图片库闸门：mode=external 而盘没插时，磁盘索引为空、所有 local 都 stat 失败，
# 本脚本会把 14 万条 URL→文件 记录一次写盘删光。必须先确认库可达。
C.guard_or_exit("s04f")
ALLOW_PRUNE = "--allow-prune" in sys.argv[1:]
# 本轮增量范围（没传 --keys-file 就是全树，行为与历史一致）
SCOPE = C.scope_read(C.scope_arg())

arts = S.load_articles(keys=C.scope_keys(SCOPE) if SCOPE else None)
body, av = S.collect_urls(arts)
allurls = list(body) + list(av)
C.log("[s04f] URL 总数 %d（正文 %d / 头像 %d）%s"
      % (len(allurls), len(body), len(av),
         "（范围模式 %d 篇）" % len(SCOPE) if SCOPE else "（全树）"))

# 头像补充：s04h 用 gravatar 哈希落盘，cravatar 原 URL 与 gravatar URL 各记一条；
# 但历史分片(av0/av1)只记了其中一种形态，会导致「文件在盘、manifest 缺条目」。
# 这里把 avatar_urls_all.json 全量纳入扫描，确保两种形态都被补齐。
# 范围模式下不追加：那是历史头像的全量清单，与本轮新增无关（头像已改统一占位图）。
av_all_path = os.path.join(C.SRC, "avatar_urls_all.json")
if not SCOPE and os.path.exists(av_all_path):
    av_all = C.read_json(av_all_path, []) or []
    seen = set(allurls)
    extra = [u for u in av_all if u not in seen]
    allurls.extend(extra)
    C.log("[s04f] 追加头像全量扫描 %d 条（去重后 URL 总数 %d）"
          % (len(extra), len(allurls)))

# 建立 hash → 实际文件 的索引（只认非空文件，0 字节是被截断的旧版本残留）
index = collections.defaultdict(list)
zeros = 0
for p in glob.glob(os.path.join(C.IMAGES_DIR, "*", "*")):
    b = os.path.basename(p)
    if "." not in b:
        continue
    try:
        if os.path.getsize(p) == 0:
            zeros += 1
            continue
    except OSError:
        continue
    index[b.rsplit(".", 1)[0]].append(p)
C.log("[s04f] 磁盘非空文件 %d，hash 索引 %d（0 字节残留 %d 个）"
      % (sum(len(v) for v in index.values()), len(index), zeros))

mpath = os.path.join(C.SRC, "image_manifest.json")
man = C.read_json(mpath, {}) or {}

added = 0
def _hash(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


def _ext(u):
    """URL 原始扩展名（不含点）；.jpeg 归一为 jpg。"""
    e = os.path.splitext(u.split("?")[0])[1].lower()
    if e == ".jpeg":
        e = ".jpg"
    return e.lstrip(".") if e in (".jpg", ".png", ".gif", ".webp") else ""


def _pick(cands, ue):
    """原扩展名优先取非空文件，其余扩展名作降级回落。

    ⚠ 同一哈希下可能并存 .gif（原图）与 .jpg（早期 GIF 转帧的静态首帧），
    按目录顺序取第一个非空会取到降级帧，归档丢动画（§1.37）。
    """
    order = [ue] if ue else []
    for c in (cands or []):
        e = c.rsplit(".", 1)[-1].lower()
        if e not in order:
            order.append(e)
    for e in order:
        for c in (cands or []):
            if c.rsplit(".", 1)[-1].lower() != e:
                continue
            try:
                if os.path.getsize(c) > 0:
                    return c
            except OSError:
                pass
    return None

for u in allurls:
    h = _hash(u)
    cands = index.get(h)
    # 头像特例：cravatar.cn 与 www.gravatar.com 是同一张图的两种 URL，
    # 文件按 gravatar URL 的哈希落盘 → cravatar 条目需按 gravatar 哈希反查
    if not cands and "cravatar.cn" in u:
        try:
            gh = u.split("/avatar/")[1].split("?")[0]
            cands = index.get(_hash("https://www.gravatar.com/avatar/%s?s=96" % gh))
        except Exception:
            cands = None
    if not cands:
        continue
    ent = man.get(u) or {}
    if ent.get("local"):
        fp0 = C.img_disk_path(ent["local"])
        # 只有「文件存在且非空」才算已就位；指向 0 字节空壳的条目需要重指向有效文件。
        # 另：当前指向非原扩展名、且盘上已有原扩展名文件时，也必须重指向（§1.37）
        try:
            ok0 = os.path.exists(fp0) and os.path.getsize(fp0) > 0
        except OSError:
            ok0 = False
        if ok0:
            bare = ""
            for c in (cands or []):
                try:
                    if c.rsplit(".", 1)[-1].lower() == _ext(u) and os.path.getsize(c) > 0:
                        bare = c
                        break
                except OSError:
                    continue
            le = os.path.splitext(fp0)[1].lower().lstrip(".")
            if not bare or le == _ext(u):
                continue
    # 取磁盘上真实存在的那个（可能扩展名与原记录不同）。
    # 原扩展名优先：GIF 原图不得被静态帧顶替（§1.37）
    p = _pick(cands, _ext(u))
    if p is None:
        continue
    e2 = {"local": C.img_key_from_disk(p),          # 台账键必须是 images/…（见 common 里那段注释）
          "bytes": os.path.getsize(p),
          "ctype": "image/" + p.rsplit(".", 1)[-1].lower(),
          "ok": True}
    # 头像：判定是否为「用户未设置头像」的默认占位图
    if "avatar" in u or "cravatar" in u or "gravatar" in u:
        try:
            with open(p, "rb") as f:
                d = hashlib.md5(f.read()).hexdigest()[:12]
            e2["is_default_avatar"] = (d in ("5911f5b37888", "80cde2f6b08e"))
        except OSError:
            pass
    man[u] = e2
    added += 1

# 清理/重指向：manifest 标记成功但
#   (a) 文件已不存在 → 尝试按哈希找同哈希的有效文件重指向；找不到才删条目
#   (b) 文件为 0 字节空壳（早期 GIF 截断）→ 优先重指向同哈希的有效压缩版；
#       确实没有有效文件才删条目（页面降级为外链/占位，避免破图）
# 增量本机模式（mode=incremental，历史图已被移到别的电脑）：(a) 是常态而非失效，
# 这类条目一律原样保留（它们是 URL→外部库文件 的唯一台账），只能被 0 字节分支处理。
new_only = C.new_images_only()
if new_only:
    C.log("[s04f] 图片库模式=incremental：本机文件缺失的条目视为「在外部库」，不删除、不重指向")
stale = 0
empty = 0
repointed = 0
away = 0
for u, v in list(man.items()):
    loc = (v or {}).get("local")
    if not loc:
        continue
    fp = C.img_disk_path(loc)
    ok_exists = os.path.exists(fp)
    try:
        ok_size = ok_exists and os.path.getsize(fp) > 0
    except OSError:
        ok_size = False
    if ok_size:
        continue
    if new_only and not ok_exists:
        away += 1
        continue

    # 找同哈希下的有效文件
    h = _hash(u)
    cands = index.get(h)
    if not cands and "cravatar.cn" in u:
        try:
            gh = u.split("/avatar/")[1].split("?")[0]
            cands = index.get(_hash("https://www.gravatar.com/avatar/%s?s=96" % gh))
        except Exception:
            cands = None
    good = _pick(cands, _ext(u))
    if good:
        nv = dict(v)
        nv["local"] = C.img_key_from_disk(good)
        nv["bytes"] = os.path.getsize(good)
        nv["ctype"] = "image/" + good.rsplit(".", 1)[-1].lower()
        nv["ok"] = True
        man[u] = nv
        repointed += 1
        continue

    # 无有效文件可指 → 删条目
    del man[u]
    if ok_exists:
        empty += 1
    else:
        stale += 1

# 清理熔断：单轮拟删除的条目数超阈值即中止且不写盘。正常增量里这个数字应该是 0；
# 它变大说明图片库根本不可见（拔盘 / 迁移没跑完 / junction 被真实目录顶替），
# 写下去就是不可逆地抹掉 URL→本地文件 台账。
dropped = stale + empty
cap = int(C.ST.load_cfg().get("prune_circuit_breaker") or 0)
if cap and dropped > cap and not ALLOW_PRUNE:
    _st, _cfg, _detail = C.store_state()
    C.log("[s04f] 熔断：本轮拟清理 %d 条（失效 %d / 0 字节 %d）> 上限 %d，**未写盘**。"
          " 图片库状态=%s（%s）；确认确实要清理再加 --allow-prune"
          % (dropped, stale, empty, cap, _st, _detail))
    print("S04F_BLOCKED_PRUNE %d>%d" % (dropped, cap))
    sys.exit(7)

C.write_json(mpath, man)
ok = sum(1 for v in man.values() if v.get("local"))
C.log("[s04f] 重建完成：新增 %d 条，重指向有效文件 %d 条，清理失效 %d 条，清理 0 字节空壳 %d 条"
      "，保留在外部库的条目 %d 条，manifest 有本地文件 %d 条"
      % (added, repointed, stale, empty, away, ok))
print("REBUILD_OK %d %d %d %d %d" % (added, repointed, stale, empty, ok))
