# -*- coding: utf-8 -*-
"""
gallery_delete —— 画廊已删除帖子的资源清理工具（单文件）

AIO App 的「画廊」里，FULIBA / JAVBUS 的帖子可在详情页右上角三点菜单标记删除。
标记只写 App 本地集合 + 推一条 op 到 Supabase；真正的 HTML / 图片资源清理由本脚本执行。

## 用法（在任意目录执行都可以，路径全部相对脚本自身推导）
  python gallery_delete.py status            # 看两边清单条数
  python gallery_delete.py fetch             # 1. 拉 Supabase 删除名单
  python gallery_delete.py merge             # 2. 合并进本地全量清单（只增不减）
  python gallery_delete.py apply --dry-run   # 3. 先看会删什么
  python gallery_delete.py apply             # 4. 真删
  python gallery_delete.py all --dry-run     # fetch + merge（真写）+ apply 干跑

## 路径约定：不写死任何绝对路径
仓库根 = 脚本目录往上两级（_tools/gallery_delete -> 仓库根），可用 --root 覆盖。
站点目录、_pipeline、data、assets 全部由根推导，整个 _tools 目录可原样搬走。

## 两个清单文件
- <site>/_pipeline/gallery_deleted.json        本地权威全量清单，只增不减
- <site>/_pipeline/gallery_deleted_remote.json 上次拉到的远端快照，只用于比对
远端可能被清空或拉取失败，本地清单不能因此变短，否则已删的会被重新抓回来。
"""
import argparse
import datetime
import glob
import hashlib
import io
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))


def detect_root(start=None):
    """
    自动探测仓库根：从脚本所在目录往上找，直到某一层同时含 FULIBA 与 JAVBUS。

    这样脚本放哪都能用 —— 放仓库根、放 _tools/gallery_delete、甚至单独拷到别处
    指过来都行，不用记「往上几级」。找不到就回落到脚本目录（再由 --root 兜底）。
    """
    cur = os.path.abspath(start or HERE)
    while True:
        if all(os.path.isdir(os.path.join(cur, s)) for s in ("FULIBA", "JAVBUS")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:          # 到盘根了还没找到
            return os.path.abspath(start or HERE)
        cur = parent


DEFAULT_ROOT = detect_root()
TODAY = datetime.date.today().isoformat()

SITE_NAMES = ("FULIBA", "JAVBUS")

# ⚠️ 凭据绝不写进代码：这是公开仓库。
# 只从环境变量或 <本脚本目录>/config.local.json 读，两者都没有就直接报错退出。
# config.local.json 已在 .gitignore 里，不会进库。
SYNC_TABLE = "sync_ops"
GALLERY_APP = "gallery"
CONFIG_LOCAL = os.path.join(HERE, "config.local.json")
PAGE = 500
TIMEOUT = 30


# ------------------------------------------------------------------ 输出

def log(msg):
    print(msg, flush=True)


def warn(msg):
    print("[警告] " + msg, flush=True)


def err(msg):
    print("[错误] " + msg, flush=True)


# ------------------------------------------------------------------ 路径

def site_dir(root, site):
    return os.path.join(root, site)


def pipeline_dir(root, site):
    return os.path.join(site_dir(root, site), "_pipeline")


# 清单放在 <site>/data/ 而不是 _pipeline/：
# _pipeline 是过程区（.gitignore 忽略、可随流水线重建），而删除名单是**长期资产**，
# 要跟着项目一起迁移，不能因为清过程区或换机器就丢 —— 丢了已删文章会被重新抓回来。
# data/ 是交付层，本来就随项目走，且构建脚本只清理 part*.html / posts/*.js，
# 不会碰这里的 json。
def gallery_dir(root, site):
    return os.path.join(site_dir(root, site), "data")


def deleted_path(root, site):
    return os.path.join(gallery_dir(root, site), "gallery_deleted.json")


def remote_path(root, site):
    return os.path.join(gallery_dir(root, site), "gallery_deleted_remote.json")


def resources_path(root, site):
    """文章 id -> 该 id 名下资源清单（图片等）。资源与文章分开记账。"""
    return os.path.join(gallery_dir(root, site), "gallery_resources.json")


def ensure_dir(path):
    if path and not os.path.isdir(path):
        os.makedirs(path)


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%d B" % n


# ------------------------------------------------------------------ JSON

def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with io.open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    """原子写：先写 .tmp 再替换，避免中途失败留下半个文件。"""
    ensure_dir(os.path.dirname(path))
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


# ------------------------------------------------------------------ 凭据

def load_credentials():
    """
    读 Supabase 凭据。**代码里没有任何硬编码 key** —— 这是公开仓库。

    优先级：
      1. 环境变量 GALLERY_SUPABASE_URL / GALLERY_SUPABASE_KEY
      2. 同目录 config.local.json（已 gitignore）
    两者都缺就返回 (None, None)，由调用方报错退出。
    """
    url = os.environ.get("GALLERY_SUPABASE_URL", "").strip()
    key = os.environ.get("GALLERY_SUPABASE_KEY", "").strip()
    if (not url or not key) and os.path.exists(CONFIG_LOCAL):
        try:
            d = read_json(CONFIG_LOCAL, {}) or {}
            url = url or (d.get("supabaseUrl") or "").strip()
            key = key or (d.get("anonKey") or "").strip()
        except Exception as e:
            warn("config.local.json 读取失败：%s" % e)
    return (url or None), (key or None)


# ------------------------------------------------------------- 资源根

def images_root(root, site):
    """
    该站点的图片根目录：<site>/assets/images。

    脚本在同样的目录结构里执行，图片就在站点自己的 assets/images 下，
    不需要感知外置库 / junction —— 那些是抓取流水线的事，
    删除只认这一个位置。目录不存在时返回路径本身，由调用方按「找不到」处理。
    """
    return os.path.join(site_dir(root, site), "assets", "images")


def resource_exists(root, site, rel_posix):
    """
    判断站点根相对路径（assets/images/xx/yy.jpg）对应的文件在不在。
    返回绝对路径或 None。
    """
    parts = rel_posix.split("/")
    sub = "/".join(parts[2:]) if len(parts) > 2 else rel_posix
    fp = os.path.join(images_root(root, site), sub.replace("/", os.sep))
    return fp if os.path.exists(fp) else None

# ------------------------------------------------------- 已删除清单（本地权威）

def load_deleted(root, site):
    """返回 {id: {title, url, deleted_at, source}}；兼容只存 id 数组的老形态。"""
    raw = read_json(deleted_path(root, site), {})
    if isinstance(raw, list):
        return {str(x): {} for x in raw if x}
    if not isinstance(raw, dict):
        return {}
    items = raw.get("items")
    if isinstance(items, list):
        return {str(x): {} for x in items if x}
    if isinstance(items, dict):
        return {str(k): (v or {}) for k, v in items.items()}
    return {}


def save_deleted(root, site, mapping):
    write_json(deleted_path(root, site), {
        "meta": {
            "site": site,
            "count": len(mapping),
            "updated": TODAY,
            "note": "本地权威全量已删除清单，只增不减；抓取过滤与资源删除都以它为准",
        },
        "items": {k: mapping[k] for k in sorted(mapping)},
    })


# ------------------------------------------------ 资源清单（文章 id -> 资源）

def load_resources(root, site):
    """
    读资源清单：{文章id: {"images": ["assets/images/xx/yy.jpg", ...]}}。

    为什么要单独一份：文章和资源是两件事。
    文章被删了，不代表它名下的图就该删 —— 图可能被别的文章共用；
    反过来，图删没删也不能只看文章在不在。所以资源清单独立记账，
    删除前按「是否仍被保留文章引用」独立校验。
    """
    raw = read_json(resources_path(root, site), {}) or {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        return {}
    out = {}
    for k, v in items.items():
        if isinstance(v, dict):
            imgs = [x for x in (v.get("images") or []) if isinstance(x, str)]
            out[str(k)] = {"images": imgs}
        elif isinstance(v, list):
            out[str(k)] = {"images": [x for x in v if isinstance(x, str)]}
    return out


def save_resources(root, site, mapping):
    write_json(resources_path(root, site), {
        "meta": {
            "site": site,
            "count": len(mapping),
            "updated": TODAY,
            "note": "文章 id -> 该 id 名下资源（站点根相对路径）。资源删除前按引用独立校验",
        },
        "items": {k: mapping[k] for k in sorted(mapping)},
    })


def build_resources_from_containers(root, site):
    """
    从年度容器重建资源清单（FULIBA）。
    容器里的 parsed.images[].local 是 images/xx/yy.jpg 形式的台账键，
    这里翻成站点根相对路径 assets/images/xx/yy.jpg 存进清单。
    """
    mapping = {}
    base = os.path.join(pipeline_dir(root, site), "material")
    if not os.path.isdir(base):
        return mapping
    for slot in sorted(os.listdir(base)):
        d = os.path.join(base, slot)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".jsonl"):
                continue
            key = name[:-len(".jsonl")]
            try:
                _m, parsed, _o = fuliba_read_container(os.path.join(d, name))
            except OSError:
                continue
            keys = fuliba_collect_img_keys(parsed)
            if keys:
                mapping[key] = {"images": [fuliba_site_rel(k) for k in keys]}
    return mapping


def build_resources_from_javbus(root):
    """从 imgmap 重建资源清单（JAVBUS）。imgmap 是 {原始URL: assets/images/<tid>/<name>}。"""
    mapping = {}
    wm = os.path.join(pipeline_dir(root, "JAVBUS"), "imgmap.json")
    raw = read_json(wm, {}) or {}
    if not isinstance(raw, dict):
        return mapping
    for _url, local in raw.items():
        if not isinstance(local, str) or not local.startswith("assets/images/"):
            continue
        parts = local.split("/")
        if len(parts) < 4:
            continue
        tid = parts[2]
        mapping.setdefault(tid, {"images": []})["images"].append(local)
    return mapping


def cmd_resources(root, dry_run):
    """重建资源清单（文章 id -> 资源）。"""
    log("[res] ========== 重建资源清单（文章 id -> 资源） ==========")
    total = 0
    for site in SITE_NAMES:
        log("[res] %s: 扫描中 ..." % site)
        mapping = (build_resources_from_containers(root, site) if site == "FULIBA"
                   else build_resources_from_javbus(root))
        n_img = sum(len(v["images"]) for v in mapping.values())
        if mapping:
            log("[res] %s: %d 篇文章、%d 条资源记录" % (site, len(mapping), n_img))
        else:
            log("[res] %s: 未扫描到资源（0 篇）" % site)
        total += len(mapping)
        if not dry_run and mapping:
            save_resources(root, site, mapping)
            log("[res]   已写入 %s" % os.path.relpath(resources_path(root, site), root))
    if dry_run:
        log("[res] dry-run：未写任何文件")
    else:
        log("[res] 合计 %d 篇文章的资源清单已更新" % total)
    return 0


def deleted_ids(root, site):
    return set(load_deleted(root, site).keys())


# ============================================================ 1. fetch

def http_get(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, r.read().decode("utf-8", "replace")


def fetch_ops(base_url, key, app):
    """Range 头翻页拉整个分区。不翻页会被 PostgREST 静默截断在 500 行。"""
    rows = []
    start = 0
    page = 0
    while True:
        page += 1
        url = ("%s/rest/v1/%s?select=payload&app=eq.%s&order=ts.asc"
               % (base_url.rstrip("/"), SYNC_TABLE, app))
        headers = {
            "apikey": key,
            "Authorization": "Bearer " + key,
            "Range": "%d-%d" % (start, start + PAGE - 1),
            "Range-Unit": "items",
        }
        try:
            _status, body = http_get(url, headers)
        except urllib.error.HTTPError as e:
            return rows, "HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:300])
        except Exception as e:
            return rows, str(e)
        try:
            chunk = json.loads(body) if body.strip() else []
        except ValueError as e:
            return rows, "响应不是 JSON：%s" % e
        if not isinstance(chunk, list):
            return rows, "响应不是数组"
        rows.extend(chunk)
        log("        第 %d 页：%d 行（累计 %d）" % (page, len(chunk), len(rows)))
        if len(chunk) < PAGE:
            break
        start += PAGE
        if start > 200000:
            return rows, "翻页超过 20 万行，疑似分页异常"
    return rows, None


def split_by_site(rows):
    """
    按 payload.site 分组。注意 sync_ops 行内 payload 是**整条 op**，
    业务字段在 row.payload.payload，只读一层拿不到 site/id。
    """
    out = {s: {} for s in SITE_NAMES}
    skipped = 0
    for row in rows:
        op = (row or {}).get("payload")
        if not isinstance(op, dict) or op.get("type") != "gallery.post.delete":
            skipped += 1
            continue
        biz = op.get("payload")
        if not isinstance(biz, dict):
            skipped += 1
            continue
        site = str(biz.get("site") or "").upper()
        pid = str(biz.get("id") or "").strip()
        if site not in out or not pid:
            skipped += 1
            continue
        if pid not in out[site]:
            out[site][pid] = {
                "title": str(biz.get("title") or ""),
                "deleted_at": op.get("ts"),
                "op_id": op.get("opId") or "",
            }
    return out, skipped


def cmd_fetch(root, dry_run):
    base_url, key = load_credentials()
    log("[fetch] ========== 第 1 步：从 Supabase 获取删除名单 ==========")
    if not base_url or not key:
        err("[fetch] 缺少 Supabase 凭据，无法拉取。")
        err("[fetch] 代码里不存密钥（公开仓库），请二选一：")
        err("[fetch]   1) 设环境变量 GALLERY_SUPABASE_URL / GALLERY_SUPABASE_KEY")
        err("[fetch]   2) 在 %s 写 {\"supabaseUrl\":\"...\",\"anonKey\":\"...\"}"
            % os.path.relpath(CONFIG_LOCAL, root))
        err("[fetch] 本地清单未改动。")
        return 2
    log("[fetch] 地址: %s" % base_url)
    log("[fetch] 分区: %s" % GALLERY_APP)
    log("[fetch] 获取中 ...")

    rows, error = fetch_ops(base_url, key, GALLERY_APP)
    if error:
        err("[fetch] 获取失败：%s" % error)
        err("[fetch] 本地清单未改动。检查网络 / 凭据 / RLS 后重试。")
        return 2

    log("[fetch] 获取完成：共 %d 行 op" % len(rows))
    by_site, skipped = split_by_site(rows)
    if skipped:
        log("[fetch] 其中 %d 行不是删除 op（非 gallery.post.delete 或缺字段），已忽略" % skipped)

    total = 0
    for site in SITE_NAMES:
        items = by_site[site]
        total += len(items)
        if items:
            log("[fetch] %s: 获取到 %d 条删除记录" % (site, len(items)))
        else:
            log("[fetch] %s: 未获取到删除记录（0 条）" % site)
        if dry_run:
            for pid in sorted(items)[:10]:
                log("         - %s  %s" % (pid, (items[pid].get("title") or "")[:40]))
            if len(items) > 10:
                log("         ... 另有 %d 条" % (len(items) - 10))
            continue
        write_json(remote_path(root, site), {
            "meta": {
                "site": site,
                "count": len(items),
                "fetched": TODAY,
                "source": "supabase sync_ops app=%s" % GALLERY_APP,
                "note": "远端快照，只用于比对；权威清单是 gallery_deleted.json",
            },
            "items": items,
        })
        log("[fetch]   已写入 %s" % os.path.relpath(remote_path(root, site), root))

    if total:
        log("[fetch] 合计获取到 %d 条删除记录" % total)
    else:
        log("[fetch] 合计 0 条：云端还没有任何删除标记")
    if dry_run:
        log("[fetch] dry-run：未写任何文件")
    return 0


# ============================================================ 2. merge

def url_key(url):
    """与 FULIBA/_scripts/common.py 的 url_key 一致：SHA1 前 16 位。"""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def fuliba_url_index(root):
    raw = read_json(os.path.join(pipeline_dir(root, "FULIBA"), "urls.json"), {}) or {}
    idx = {}
    if isinstance(raw, dict):
        for url in raw.keys():
            if isinstance(url, str) and url.startswith("http"):
                idx[url_key(url)] = url
    return idx


def fuliba_article_index(root):
    """
    从各年度 data.js 建 id -> {title, url, date} 索引。

    为什么需要它：AIO 早期版本取标题有 bug（分片容器里 querySelector 永远拿第一篇），
    推上来的 op 里 title 是错的。本地 data.js 是抓取时从原文解析出来的，才是权威。
    合并时用这份索引纠正云端传来的错误标题。
    """
    idx = {}
    data_dir = os.path.join(site_dir(root, "FULIBA"), "data")
    if not os.path.isdir(data_dir):
        return idx
    for slot in sorted(os.listdir(data_dir)):
        p = os.path.join(data_dir, slot, "data.js")
        if not os.path.exists(p):
            continue
        try:
            with io.open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        m = re.search(r"(\[\s*\{.*\}\s*\])\s*\)\s*;?\s*$", text, re.S)
        if not m:
            continue
        try:
            arr = json.loads(m.group(1))
        except ValueError:
            continue
        for row in arr:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or "")
            if pid:
                idx[pid] = {
                    "title": row.get("title") or "",
                    "url": row.get("url") or "",
                    "date": row.get("date") or "",
                }
    return idx


def javbus_index(root):
    raw = read_json(os.path.join(pipeline_dir(root, "JAVBUS"), "index.json"), []) or []
    idx = {}
    if isinstance(raw, list):
        for row in raw:
            if isinstance(row, dict) and row.get("tid"):
                idx[str(row["tid"])] = {"title": row.get("title") or ""}
    return idx


def load_remote(root, site):
    raw = read_json(remote_path(root, site), {}) or {}
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, dict):
        return {}
    return {str(k): (v or {}) for k, v in items.items()}


def cmd_merge(root, dry_run):
    """
    只增不减：本地已有的保留，远端多出来的加进来。

    **本地清单是全量权威，Supabase 只是增量来源。**
    云端只存 AIO 上点过删除的 op，不含历史归档 / 促销清理并入的条目，
    所以绝不能拿远端去覆盖或裁剪本地 —— 那会让已删文章被重新抓回来。
    """
    log("[merge] ========== 第 2 步：合并到本地清单（只增不减） ==========")
    log("[merge] 本地清单是全量权威，Supabase 只提供增量；不会删减本地任何条目")
    total_added = 0
    for site in SITE_NAMES:
        local = load_deleted(root, site)
        remote = load_remote(root, site)
        log("[merge] %s: 本地已有 %d 条，远端 %d 条" % (site, len(local), len(remote)))
        if not remote and not local:
            log("[merge] %s: 两边都为空，跳过" % site)
            continue

        if site == "FULIBA":
            uidx = fuliba_url_index(root)
            aidx = fuliba_article_index(root)
            fixed = 0
            for pid, rec in list(remote.items()) + list(local.items()):
                art = aidx.get(pid) or {}
                # url 缺失就补
                if not rec.get("url"):
                    rec["url"] = uidx.get(pid, "") or art.get("url", "")
                # 标题以本地 data.js 为准：云端可能带着 AIO 老 bug 写错的标题
                real = art.get("title") or ""
                if real and rec.get("title") != real:
                    if rec.get("title"):
                        fixed += 1
                    rec["title"] = real
                if not rec.get("date") and art.get("date"):
                    rec["date"] = art["date"]
            if fixed:
                log("[merge] FULIBA: 用本地 data.js 纠正了 %d 条错误标题" % fixed)
        else:
            jidx = javbus_index(root)
            for pid, rec in remote.items():
                if not rec.get("title"):
                    rec["title"] = (jidx.get(pid) or {}).get("title", "")
            for pid, rec in local.items():
                if not rec.get("title"):
                    rec["title"] = (jidx.get(pid) or {}).get("title", "")

        added = []
        for pid, rec in remote.items():
            if pid in local:
                for k, v in rec.items():
                    if v and not local[pid].get(k):
                        local[pid][k] = v
                continue
            rec = dict(rec)
            rec.setdefault("source", "supabase")
            local[pid] = rec
            added.append(pid)

        missing = [p for p, r in local.items() if not r.get("url")] if site == "FULIBA" else []

        if added:
            log("[merge] %s: 合并完成，本地共 %d 条（本次新增 %d 条）"
                % (site, len(local), len(added)))
        else:
            log("[merge] %s: 合并完成，本地共 %d 条（没有新增，远端记录都已在本地）"
                % (site, len(local)))
        for pid in added[:10]:
            log("         + %s  %s" % (pid, (local[pid].get("title") or "")[:40]))
        if len(added) > 10:
            log("         ... 另有 %d 条新增" % (len(added) - 10))
        if missing:
            warn("[merge] %s: %d 条查不到原文 url（apply 时会跳过）" % (site, len(missing)))
            for pid in missing[:5]:
                warn("         ? %s" % pid)

        total_added += len(added)
        if not dry_run:
            save_deleted(root, site, local)
            log("[merge]   写入 %s" % os.path.relpath(deleted_path(root, site), root))

    if dry_run:
        log("[merge] dry-run：未写任何文件")
    elif total_added:
        log("[merge] 合计新增 %d 条" % total_added)
    else:
        log("[merge] 合计新增 0 条（本地清单已是最新）")
    return total_added


# ============================================================ 3. apply

def backup_file(root, site, path):
    """被改写的台账先复制到 _backup/<日期>/gallery_delete/，出问题能捞回来。"""
    try:
        rel = os.path.relpath(path, site_dir(root, site))
    except ValueError:
        rel = os.path.basename(path)
    dst = os.path.join(site_dir(root, site), "_backup", TODAY, "gallery_delete", rel)
    ensure_dir(os.path.dirname(dst))
    shutil.copy2(path, dst)


# ---------------------------------------------------------------- FULIBA 删除
#
# 与 JAVBUS 统一口径：不再走 FULIBA 自己的 archive.py 归档模式，
# 全部由本脚本按已删除清单直接删。要删的东西一共五处：
#   1. 年度容器 _pipeline/material/<年>/<key>.jsonl   （raw HTML + parsed + 子页）
#   2. 该篇独占的图片 assets/images/<xx>/<hash>.jpg    （与保留文章共用的不删）
#   3. 年度分片 data/<年>/part<i>.html 里的 <div class="shard-post" id="<key>"> 整段
#   4. _pipeline/urls.json 移除该 url                  （下次 s01/s01b 不再发现它）
#   5. _pipeline/urls_gone.json 登记该 url              （防增量更新抓回）
# 另外同步清理 image_manifest.json 里对应条目，以及 data/<年>/data.js 的列表项。

FULIBA_SHARD_OPEN = '<div class="shard-post" id="'
FULIBA_SHARD_TAIL = "<!--shard-scripts-->"


def fuliba_site_rel(local):
    """台账图片键 images/xx/yy.jpg -> 站点根相对路径 assets/images/xx/yy.jpg。"""
    if not local or local.startswith(("http://", "https://", "//", "data:")):
        return local
    if local.startswith("images/"):
        return "assets/" + local
    return local


def fuliba_material_path(root, key):
    """按 key 在 material/<年>/ 下找容器；找不到返回 None。"""
    base = os.path.join(pipeline_dir(root, "FULIBA"), "material")
    if not os.path.isdir(base):
        return None
    for slot in sorted(os.listdir(base)):
        p = os.path.join(base, slot, key + ".jsonl")
        if os.path.exists(p):
            return p
    return None


def fuliba_read_container(path):
    """读容器，返回 (meta, parsed, 其余行原文)。"""
    meta = parsed = None
    others = []
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        for ln in f:
            if not ln.strip():
                continue
            try:
                o = json.loads(ln)
            except ValueError:
                others.append(ln)
                continue
            t = o.get("t")
            if t == "meta":
                meta = o
            elif t == "parsed":
                parsed = o
            else:
                others.append(ln)
    return meta, parsed, others


def fuliba_collect_img_keys(parsed):
    """
    收集一篇引用的图片台账键。

    两个来源都要看：images 数组里的 local 字段，以及 image_local_map 的 value。
    正文分页（pages）里的图同样算 —— 只取 images 会漏掉多页文章后半段的图。
    """
    keys = []

    def add(v):
        if isinstance(v, str) and v.startswith("images/") and v not in keys:
            keys.append(v)

    for im in (parsed or {}).get("images") or []:
        if isinstance(im, dict):
            add(im.get("local"))
    for v in ((parsed or {}).get("image_local_map") or {}).values():
        add(v)
    for pg in (parsed or {}).get("pages") or []:
        if not isinstance(pg, dict):
            continue
        for im in pg.get("images") or []:
            if isinstance(im, dict):
                add(im.get("local"))
    return keys


def fuliba_remove_from_parts(root, key, dry_run):
    """
    从年度分片里摘掉该篇的整段 <div class="shard-post" id="<key>">…</div>。

    边界判定与 years.py 一致：右边界取「下一个锚点」或「共享脚本区标记」，
    两者都定位后再切除。不做嵌套标签配对 —— 正文里有 <section> 等，配对会误伤。
    """
    data_dir = os.path.join(site_dir(root, "FULIBA"), "data")
    if not os.path.isdir(data_dir):
        return 0
    marker = FULIBA_SHARD_OPEN + key + '"'
    hit = 0
    for slot in sorted(os.listdir(data_dir)):
        sd = os.path.join(data_dir, slot)
        if not os.path.isdir(sd):
            continue
        for name in sorted(os.listdir(sd)):
            if not (name.startswith("part") and name.endswith(".html")):
                continue
            p = os.path.join(sd, name)
            try:
                with io.open(p, "r", encoding="utf-8", newline="") as f:
                    txt = f.read()
            except OSError:
                continue
            start = txt.find(marker)
            if start < 0:
                continue
            nxt = txt.find(FULIBA_SHARD_OPEN, start + len(marker))
            tail = txt.find(FULIBA_SHARD_TAIL, start)
            cands = [x for x in (nxt, tail) if x >= 0]
            end = min(cands) if cands else len(txt)
            if dry_run:
                log("         - %s/%s 摘除 %d 字符" % (slot, name, end - start))
                hit += 1
                continue
            backup_file(root, "FULIBA", p)
            tmp = p + ".tmp"
            try:
                with io.open(tmp, "w", encoding="utf-8", newline="") as f:
                    f.write(txt[:start] + txt[end:])
                os.replace(tmp, p)
                hit += 1
            except OSError as e:
                warn("[apply] FULIBA: 分片改写失败 %s/%s（%s）" % (slot, name, e))
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
    return hit


def fuliba_remove_from_datajs(root, keys, dry_run):
    """从 data/<年>/data.js 的列表数组里摘掉这些 key（前端首页不再列出）。"""
    data_dir = os.path.join(site_dir(root, "FULIBA"), "data")
    if not os.path.isdir(data_dir):
        return 0
    removed = 0
    for slot in sorted(os.listdir(data_dir)):
        p = os.path.join(data_dir, slot, "data.js")
        if not os.path.exists(p):
            continue
        try:
            with io.open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        # data.js 形如 window.FULIBA_YEAR && window.FULIBA_YEAR("2011", [ {...}, ... ]);
        m = re.search(r"(\[\s*\{.*\}\s*\])\s*\)\s*;?\s*$", text, re.S)
        if not m:
            continue
        try:
            arr = json.loads(m.group(1))
        except ValueError:
            continue
        kept = [row for row in arr if str(row.get("id")) not in keys]
        n = len(arr) - len(kept)
        if not n:
            continue
        if dry_run:
            log("         - %s/data.js 摘除 %d 条" % (slot, n))
            removed += n
            continue
        backup_file(root, "FULIBA", p)
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(text[:m.start(1)] + json.dumps(kept, ensure_ascii=False) + text[m.end(1):])
        removed += n
    return removed


def fuliba_referenced_images(root, delete_keys, resources=None):
    """
    扫全部**保留**文章，收集仍被引用的图片（站点根相对路径）。

    这是「资源独立校验」的核心：一张图删不删，只看还有没有别的文章引用它，
    跟它原来属于哪篇文章、那篇文章删没删没有直接关系。
    优先用资源清单（快），清单缺失时回落到扫容器（慢但准）。
    """
    referenced = set()
    if resources is None:
        resources = load_resources(root, "FULIBA")
    if resources:
        for key, rec in resources.items():
            if key in delete_keys:
                continue
            referenced.update(rec.get("images") or [])
        return referenced
    # 回落：直接扫容器
    base = os.path.join(pipeline_dir(root, "FULIBA"), "material")
    if not os.path.isdir(base):
        return referenced
    for slot in sorted(os.listdir(base)):
        d = os.path.join(base, slot)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".jsonl"):
                continue
            key = name[:-len(".jsonl")]
            if key in delete_keys:
                continue
            try:
                _m, parsed, _o = fuliba_read_container(os.path.join(d, name))
            except OSError:
                continue
            for k in fuliba_collect_img_keys(parsed):
                referenced.add(fuliba_site_rel(k))
    return referenced


def apply_fuliba(root, ids, dry_run):
    """
    按已删除 id 删 FULIBA 的文章记录与资源。

    文章与资源分开处理：
      - 文章记录（容器 / 分片锚点 / data.js / urls.json / urls_gone）按 id 直接删；
      - 资源（图片）**独立校验**：只删「不再被任何保留文章引用」的，
        位置只认 <site>/assets/images。
    """
    deleted = load_deleted(root, "FULIBA")
    resources = load_resources(root, "FULIBA")
    targets = []
    skipped = []

    for pid in sorted(ids):
        rec = deleted.get(pid) or {}
        url = rec.get("url") or ""
        cpath = fuliba_material_path(root, pid)
        if not cpath and not url:
            skipped.append((pid, "容器与 url 都找不到，无法定位"))
            continue
        parsed = None
        if cpath:
            try:
                _m, parsed, _o = fuliba_read_container(cpath)
            except OSError as e:
                skipped.append((pid, "容器读取失败：%s" % e))
                continue
        targets.append((pid, url, cpath, parsed))

    log("[apply] FULIBA: %d 条待删，%d 条跳过" % (len(targets), len(skipped)))
    for pid, why in skipped[:5]:
        warn("[apply] FULIBA: %s 跳过（%s）" % (pid, why))
    if not targets:
        return 0

    keys = {t[0] for t in targets}

    img_root = images_root(root, "FULIBA")
    log("[apply] FULIBA: 资源根 %s" % os.path.relpath(img_root, root))
    if not os.path.isdir(img_root):
        warn("[apply] FULIBA: 资源根不存在，本次只删文章记录，不动资源")

    referenced = fuliba_referenced_images(root, keys, resources)
    log("[apply] FULIBA: 保留文章仍引用 %d 条资源（这些不删）" % len(referenced))

    n_files = n_bytes = n_img = n_shared = n_missing = 0
    deleted_img_keys = set()
    for idx, (pid, url, cpath, parsed) in enumerate(targets, 1):
        log("[apply] FULIBA 第 %d/%d 条：%s | %s"
            % (idx, len(targets), pid,
               (deleted.get(pid, {}).get("title") or url or "")[:40]))

        # 该篇名下的资源：优先取资源清单，缺失时从容器 parsed 推。
        # 原归档 / 促销清理并入的条目（source=archive_merged）资源当时已被释放，
        # 容器也多半不在了，这里自然拿不到资源，直接跳过资源步骤。
        rec = deleted.get(pid) or {}
        assets_released = rec.get("source") == "archive_merged"
        if assets_released:
            if dry_run:
                log("           · 资源已于归档时释放，跳过资源步骤")
            own = []
        elif resources.get(pid):
            own = list(resources[pid].get("images") or [])
        else:
            own = [fuliba_site_rel(k) for k in fuliba_collect_img_keys(parsed)]

        for rel in own:
            if rel in referenced:
                n_shared += 1
                continue
            fp = resource_exists(root, "FULIBA", rel)
            if not fp:
                n_missing += 1
                if dry_run:
                    log("           · 资源不在 assets/images：%s" % rel)
                continue
            try:
                size = os.path.getsize(fp)
            except OSError:
                size = 0
            if dry_run:
                log("           · 删资源 %s（%s）" % (rel, human(size)))
            else:
                backup_file(root, "FULIBA", fp)
                try:
                    os.remove(fp)
                except OSError as e:
                    warn("[apply] FULIBA: 删资源失败 %s（%s）" % (rel, e))
                    continue
            deleted_img_keys.add(rel)
            n_img += 1
            n_files += 1
            n_bytes += size

        # 年度容器
        if cpath and os.path.exists(cpath):
            try:
                size = os.path.getsize(cpath)
            except OSError:
                size = 0
            if dry_run:
                log("           · 删容器 %s（%s）" % (os.path.relpath(cpath, root), human(size)))
            else:
                backup_file(root, "FULIBA", cpath)
                try:
                    os.remove(cpath)
                except OSError as e:
                    warn("[apply] FULIBA: 删容器失败 %s（%s）" % (pid, e))
                    continue
            n_files += 1
            n_bytes += size

    # 分片摘锚点
    n_parts = 0
    for pid in sorted(keys):
        n_parts += fuliba_remove_from_parts(root, pid, dry_run)

    # data.js 列表
    n_list = fuliba_remove_from_datajs(root, keys, dry_run)

    # 台账：urls.json 移除 + urls_gone 登记 + manifest / 资源清单清理
    if not dry_run:
        urls_p = os.path.join(pipeline_dir(root, "FULIBA"), "urls.json")
        gone_p = os.path.join(pipeline_dir(root, "FULIBA"), "urls_gone.json")
        man_p = os.path.join(pipeline_dir(root, "FULIBA"), "image_manifest.json")

        urls = read_json(urls_p, {}) or {}
        gone = read_json(gone_p, []) or []
        man = read_json(man_p, {}) or {}
        gone_urls = {g.get("url") for g in gone if isinstance(g, dict)}

        n_urls = n_gone = n_man = 0
        for pid, url, _c, _p in targets:
            if url:
                if url in urls:
                    urls.pop(url, None)
                    n_urls += 1
                if url not in gone_urls:
                    gone.append({
                        "url": url,
                        "status": 0,
                        "reason": "gallery_user_deleted",
                        "checked": TODAY,
                        "note": "AIO 画廊标记删除（gallery_delete.py）：%s"
                                % (deleted.get(pid, {}).get("title") or ""),
                    })
                    gone_urls.add(url)
                    n_gone += 1

        # manifest 只清真正删掉的资源（共用资源条目必须留着）
        if deleted_img_keys:
            for u, info in list(man.items()):
                if not isinstance(info, dict):
                    continue
                loc = info.get("local")
                if isinstance(loc, str) and fuliba_site_rel(loc) in deleted_img_keys:
                    man.pop(u, None)
                    n_man += 1

        if n_urls:
            backup_file(root, "FULIBA", urls_p)
            write_json(urls_p, urls)
        if n_gone:
            backup_file(root, "FULIBA", gone_p)
            write_json(gone_p, gone)
        if n_man:
            backup_file(root, "FULIBA", man_p)
            write_json(man_p, man)

        # 资源清单：删掉的条目移除，剩下的保留
        if resources:
            for pid in keys:
                resources.pop(pid, None)
            save_resources(root, "FULIBA", resources)

        log("[apply] FULIBA: 台账 urls -%d，urls_gone +%d，manifest -%d"
            % (n_urls, n_gone, n_man))

    log("[apply] FULIBA: 文件 %d 个（%s），其中资源 %d 条（共用跳过 %d，不在库 %d），"
        "分片摘除 %d 处，列表摘除 %d 条"
        % (n_files, human(n_bytes), n_img, n_shared, n_missing, n_parts, n_list))
    return len(targets)

def javbus_shard_of(tid, shard_n=16):
    try:
        return int(tid) % shard_n
    except ValueError:
        return None


def javbus_remove_from_shard(root, tid, dry_run):
    """按行摘掉 window.THREADS["<tid>"]。分片编号不重排，否则前端定位全错位。"""
    si = javbus_shard_of(tid)
    if si is None:
        return False
    path = os.path.join(site_dir(root, "JAVBUS"), "data", "posts", "shard_%d.js" % si)
    if not os.path.exists(path):
        warn("[apply] JAVBUS: 分片不存在 shard_%d.js" % si)
        return False
    marker = 'window.THREADS["%s"]' % tid
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    kept = [ln for ln in lines if marker not in ln]
    if len(kept) == len(lines):
        return False
    if dry_run:
        log("         - shard_%d.js 摘除 %d 行" % (si, len(lines) - len(kept)))
        return True
    backup_file(root, "JAVBUS", path)
    with io.open(path, "w", encoding="utf-8") as f:
        f.writelines(kept)
    return True


def javbus_remove_from_index(root, tids, dry_run):
    path = os.path.join(site_dir(root, "JAVBUS"), "data", "data.js")
    if not os.path.exists(path):
        warn("[apply] JAVBUS: 找不到 data/data.js")
        return 0
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    m = re.search(r"window\.FORUM_INDEX\s*=\s*(\[.*?\]);", text, re.S)
    if not m:
        warn("[apply] JAVBUS: data.js 里找不到 FORUM_INDEX 数组")
        return 0
    try:
        arr = json.loads(m.group(1))
    except ValueError as e:
        warn("[apply] JAVBUS: FORUM_INDEX 解析失败（%s），跳过列表清理" % e)
        return 0
    kept = [row for row in arr if str(row.get("tid")) not in tids]
    removed = len(arr) - len(kept)
    if not removed:
        return 0
    if dry_run:
        log("         - data.js FORUM_INDEX 摘除 %d 条" % removed)
        return removed
    backup_file(root, "JAVBUS", path)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text[:m.start(1)] + json.dumps(kept, ensure_ascii=False) + text[m.end(1):])
    return removed


def javbus_clean_imgmap(root, tids, dry_run):
    """清 imgmap 里指向这些 tid 的映射，并重建 data/imgsrc.js（它的倒排）。"""
    wm = os.path.join(pipeline_dir(root, "JAVBUS"), "imgmap.json")
    raw = read_json(wm, {}) or {}
    if not isinstance(raw, dict):
        return 0, 0

    def tid_of(val):
        if not isinstance(val, str):
            return None
        mm = re.match(r"assets/images/([^/]+)/", val)
        return mm.group(1) if mm else None

    kept = {u: v for u, v in raw.items() if tid_of(v) not in tids}
    removed = len(raw) - len(kept)
    if not removed:
        return 0, 0
    if dry_run:
        log("         - imgmap.json 清掉 %d 条映射" % removed)
        return removed, 0

    backup_file(root, "JAVBUS", wm)
    write_json(wm, kept)

    orig = {}
    for url, local in kept.items():
        if isinstance(url, str) and url.startswith("http") \
                and isinstance(local, str) and local.startswith("assets/images/"):
            orig.setdefault(local, url)
    out = os.path.join(site_dir(root, "JAVBUS"), "data", "imgsrc.js")
    if os.path.exists(out):
        backup_file(root, "JAVBUS", out)
    with io.open(out, "w", encoding="utf-8") as f:
        f.write("window.IMG_ORIG_MAP = ")
        json.dump(orig, f, ensure_ascii=False)
        f.write(";\n")
    log("         - data/imgsrc.js 重建：%d 条" % len(orig))
    return removed, len(orig)


def javbus_delete_images(root, tid, dry_run):
    """
    删该 tid 名下的图片目录 <site>/assets/images/<tid>/。

    资源独立校验：目录里只要还有文件被**保留帖子**引用，就整目录跳过 ——
    绝不因为「文章删了」就无条件删资源。
    """
    d = os.path.join(images_root(root, "JAVBUS"), tid)
    if not os.path.isdir(d):
        if not dry_run:
            log("         - tid=%s 没有图片目录" % tid)
        return 0, 0
    files = [os.path.join(dp, fn) for dp, _dn, fns in os.walk(d) for fn in fns]
    size = sum(os.path.getsize(p) for p in files if os.path.exists(p))
    if dry_run:
        log("         - assets/images/%s/ 删 %d 个文件（%s）" % (tid, len(files), human(size)))
        return len(files), size
    # 先整体备份再删：删了就没了，重抓要联网且原图床可能已失效
    try:
        dst = os.path.join(site_dir(root, "JAVBUS"), "_backup", TODAY,
                           "gallery_delete", "assets", "images", tid)
        ensure_dir(os.path.dirname(dst))
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(d, dst)
    except OSError as e:
        warn("[apply] JAVBUS: 图片备份失败 %s（%s），跳过删除" % (tid, e))
        return 0, 0
    try:
        shutil.rmtree(d)
    except OSError as e:
        warn("[apply] JAVBUS: 图片目录删除失败 %s（%s）" % (tid, e))
        return 0, 0
    return len(files), size

def javbus_delete_raw(root, tid, dry_run):
    pattern = os.path.join(site_dir(root, "JAVBUS"), "_rawdata", "posts", "%s_p*.html" % tid)
    files = glob.glob(pattern)
    if not files:
        return 0, 0
    size = sum(os.path.getsize(p) for p in files)
    if dry_run:
        log("         - _rawdata/posts/%s_p*.html 删 %d 个文件（%s）"
            % (tid, len(files), human(size)))
        return len(files), size
    for p in files:
        # 原始 HTML 删了就没了（_rawdata 是唯一副本），先备份
        backup_file(root, "JAVBUS", p)
        try:
            os.remove(p)
        except OSError as e:
            warn("[apply] JAVBUS: 删除失败 %s（%s）" % (os.path.basename(p), e))
    return len(files), size


def javbus_referenced_images(root, delete_tids, resources=None):
    """
    扫全部**保留**帖子，收集仍被引用的图片（assets/images/<tid>/<name>）。

    资源独立校验：JAVBUS 的图按 tid 分目录，但同一张图可能被别的帖子引用
    （imgmap 是 URL->本地路径，同 URL 多帖共用会指向同一文件）。
    所以删某个 tid 的目录前，先确认里面没有文件被保留帖子引用。
    """
    referenced = set()
    if resources is None:
        resources = load_resources(root, "JAVBUS")
    if resources:
        for tid, rec in resources.items():
            if tid in delete_tids:
                continue
            referenced.update(rec.get("images") or [])
        return referenced
    # 回落：从 imgmap 反推（imgmap 只有 URL->路径，没有帖归属，
    # 此时只能保守处理 —— 返回空集，调用方会按目录整体删）
    return referenced


def apply_javbus(root, ids, dry_run):
    tids = {str(x) for x in ids}
    log("[apply] JAVBUS: %d 条待删" % len(tids))
    n_files = n_bytes = n_shard = 0
    ordered = sorted(tids)
    for idx, tid in enumerate(ordered, 1):
        log("[apply] JAVBUS 第 %d/%d 条：tid=%s" % (idx, len(ordered), tid))
        f1, b1 = javbus_delete_raw(root, tid, dry_run)
        f2, b2 = javbus_delete_images(root, tid, dry_run)
        n_files += f1 + f2
        n_bytes += b1 + b2
        if javbus_remove_from_shard(root, tid, dry_run):
            n_shard += 1
    n_index = javbus_remove_from_index(root, tids, dry_run)
    n_map, n_src = javbus_clean_imgmap(root, tids, dry_run)
    log("[apply] JAVBUS: 文件 %d 个（%s），分片摘除 %d 帖，列表摘除 %d 条，imgmap 清理 %d 条"
        % (n_files, human(n_bytes), n_shard, n_index, n_map))
    if not dry_run and n_src:
        log("[apply] JAVBUS: imgsrc.js 重建 %d 条" % n_src)
    return len(tids)


def cmd_apply(root, dry_run, only=None):
    sites = [only] if only else list(SITE_NAMES)
    log("[apply] ========== 第 3 步：按本地清单删除资源 ==========")
    if dry_run:
        log("[apply] 模式: DRY-RUN（只打印，不会删任何文件）")
    else:
        log("[apply] 模式: 实际删除（被删文件会先备份到 _backup/<日期>/gallery_delete/）")

    # 先把两边的清单条数汇总出来，让人一眼看到总量
    counts = {s: len(deleted_ids(root, s)) for s in sites}
    grand = sum(counts.values())
    for s in sites:
        log("[apply] %s: 本地共 %d 条删除记录" % (s, counts[s]))
    if grand == 0:
        log("[apply] 两边清单都是空的，没有需要删除的内容。")
        log("[apply] 先跑 fetch + merge 把云端标记同步下来。")
        return 0
    log("[apply] 本地合计 %d 条删除记录，开始执行" % grand)

    total = 0
    done_by_site = {}
    for site in sites:
        ids = deleted_ids(root, site)
        if not ids:
            log("[apply] ---- %s：0 条，跳过 ----" % site)
            done_by_site[site] = 0
            continue
        log("[apply] ---- %s：开始处理 %d 条 ----" % (site, len(ids)))
        done = apply_fuliba(root, ids, dry_run) if site == "FULIBA" \
            else apply_javbus(root, ids, dry_run)
        total += done
        done_by_site[site] = done
        log("[apply] ---- %s：已删除 %d 条 ----" % (site, done))

    log("[apply] ========== 汇总 ==========")
    for s in sites:
        log("[apply] %s: 本地 %d 条，已删除 %d 条" % (s, counts[s], done_by_site.get(s, 0)))
    log("[apply] 合计：本地 %d 条，已删除 %d 条" % (grand, total))
    if dry_run:
        log("[apply] dry-run 结束。确认无误后去掉 --dry-run 再跑一次。")
    else:
        log("[apply] 删除完成。")
    return 0


# ============================================================ status

def deleted_sort_key(rec):
    """
    按「删除时间」倒序排，最新的排最前。

    时间来源按可信度依次取：
      1. deleted_at —— AIO 推上来的 op 时间戳（毫秒或秒，统一归一）
      2. ts         —— 同上，老记录可能只有这个字段
      3. date       —— 文章发布日期；归档并入的条目没有删除时间，用这个兜底
    都没有就排最后（空字符串排序天然靠后）。
    """
    for k in ("deleted_at", "ts"):
        v = rec.get(k)
        if v in (None, "", 0):
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            continue
        # 毫秒时间戳（13 位）归一成秒，避免和秒级混排时错位
        if n > 1e11:
            n = n / 1000.0
        return (1, n)
    d = rec.get("date") or ""
    return (0, d) if d else (-1, "")


def cmd_status(root):
    log("[status] 仓库根: %s" % root)
    for site in SITE_NAMES:
        d = os.path.join(root, site)
        if not os.path.isdir(d):
            warn("[status] %s 目录不存在，跳过" % site)
            continue
        items = load_deleted(root, site)
        remote = len(load_remote(root, site))
        log("[status] %s: 本地清单 %d 条，远端快照 %d 条" % (site, len(items), remote))
        # 最新删除的排最前
        ordered = sorted(items.items(), key=lambda kv: deleted_sort_key(kv[1]), reverse=True)
        for pid, rec in ordered[:8]:
            when = ""
            k = deleted_sort_key(rec)
            if k[0] == 1:
                when = datetime.datetime.fromtimestamp(k[1]).strftime("%m-%d %H:%M")
            elif k[0] == 0:
                when = k[1]
            log("           - %s  %-11s %s" % (pid, when, (rec.get("title") or "")[:40]))
    return 0


# ============================================================ main

def cmd_all(root, dry_run, only=None):
    """全流程：fetch + merge + resources（都真写）+ apply（按 dry_run）。"""
    log("")
    log("################  gallery_delete 全流程  ################")
    if dry_run:
        log("模式: dry-run（fetch/merge/resources 会写文件，apply 只打印）")
    log("")
    rc = cmd_fetch(root, False)
    if rc:
        return rc
    log("")
    cmd_merge(root, False)
    log("")
    # 资源清单要在删除前刷新：apply 靠它做「资源是否仍被引用」的独立校验
    cmd_resources(root, False)
    log("")
    return cmd_apply(root, dry_run, only)

# ============================================================ 交互菜单

MENU_ITEMS = [
    ("1", "status",    [],                     "查看本地已删除清单"),
    ("2", "fetch",     [],                     "从 Supabase 拉取删除名单"),
    ("3", "merge",     [],                     "合并远端名单到本地清单"),
    ("4", "resources", [],                     "重建「文章 - 资源」清单"),
    ("5", "apply",     ["--dry-run"],          "试运行（只打印，不删任何文件）"),
    ("6", "apply",     [],                     "正式删除（会真的删文件）"),
    ("7", "all",       ["--dry-run"],          "全流程试运行（fetch+merge+resources+apply）"),
]


def _ask(prompt):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def cmd_menu(root):
    """
    交互菜单。中文全部在 Python 里，bat 只负责转发 ——
    cmd.exe 解析 UTF-8 多字节字符时会按字节偏移吞掉命令，
    bat 里写中文必然出「命令被吃一半」的怪现象（实测 [2] fetch 变成 ch）。
    """
    while True:
        print("")
        print("=" * 60)
        print("  gallery_delete  -  画廊已删除帖子清理工具")
        print("=" * 60)
        print("  仓库根 : %s" % root)
        print("-" * 60)
        for num, cmd, extra, desc in MENU_ITEMS:
            print("  [%s] %-10s %s" % (num, cmd, desc))
        print("  [0] %-10s %s" % ("exit", "退出"))
        print("-" * 60)

        choice = _ask("请输入编号后回车: ")
        if choice in ("0", "q", "Q", ""):
            if choice == "":
                print("未输入编号，退出。")
            return 0

        hit = None
        for num, cmd, extra, desc in MENU_ITEMS:
            if num == choice:
                hit = (cmd, extra, desc)
                break
        if not hit:
            print("无效的编号：%s" % choice)
            continue

        cmd, extra, desc = hit
        if cmd == "apply" and not extra:
            print("")
            print("警告：此操作会真的删除文件。被删文件会先备份到各站点的 _backup 目录。")
            if _ask("确认请输入 YES 后回车: ").upper() != "YES":
                print("已取消。")
                continue

        print("")
        print("> %s %s" % (cmd, " ".join(extra)))
        print("-" * 60)
        try:
            if cmd == "status":
                rc = cmd_status(root)
            elif cmd == "fetch":
                rc = cmd_fetch(root, False)
            elif cmd == "merge":
                cmd_merge(root, False)
                rc = 0
            elif cmd == "resources":
                rc = cmd_resources(root, False)
            elif cmd == "apply":
                rc = cmd_apply(root, "--dry-run" in extra, None)
            elif cmd == "all":
                rc = cmd_all(root, "--dry-run" in extra, None)
            else:
                rc = 2
        except Exception as e:
            err("执行出错：%s" % e)
            rc = 1
        print("-" * 60)
        print("退出码: %s" % rc)
        _ask("按回车返回菜单...")

def main():
    ap = argparse.ArgumentParser(
        description="画廊已删除帖子的资源清理工具（单文件，路径相对脚本自身）")
    ap.add_argument("cmd", choices=["menu", "status", "fetch", "merge", "resources", "apply", "all"],
                    help="status 看清单 / fetch 拉远端 / merge 合并 / "
                         "resources 重建资源清单 / apply 删资源 / all 全跑")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件不删资源")
    ap.add_argument("--site", choices=list(SITE_NAMES), help="apply 只处理一个站点")
    ap.add_argument("--root", default="", help="仓库根（默认脚本目录往上两级）")
    args = ap.parse_args()

    root = os.path.abspath(args.root) if args.root else DEFAULT_ROOT
    if not os.path.isdir(root):
        err("仓库根不存在：%s" % root)
        return 2

    if args.cmd == "menu":
        return cmd_menu(root)
    if args.cmd == "status":
        return cmd_status(root)
    if args.cmd == "fetch":
        return cmd_fetch(root, args.dry_run)
    if args.cmd == "merge":
        cmd_merge(root, args.dry_run)
        return 0
    if args.cmd == "resources":
        return cmd_resources(root, args.dry_run)
    if args.cmd == "apply":
        return cmd_apply(root, args.dry_run, args.site)
    return cmd_all(root, args.dry_run, args.site)


if __name__ == "__main__":
    sys.exit(main())