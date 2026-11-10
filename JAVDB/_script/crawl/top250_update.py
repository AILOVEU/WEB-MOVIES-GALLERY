# -*- coding: utf-8 -*-
"""TOP250 增量更新 + 快照版本管理（JAVDB）

子命令：
  init                把当前 data/site/top250.js 登记为第一个基线版本（可被 switch 回退）
  check               抓取 javdb.com/rankings/top 的实时总榜，与当前激活版本比对，只汇报
                      差异（新增/退出/名次变动数量），不写盘；草稿落 _pipeline/top250_draft.json
  commit              重新抓取并新增一个版本（覆盖 all 总榜，其余 variant 原样保留），生成快照、
                      更新激活指针、重写 data/site/top250.js、bump 缓存版本号
  list                列出所有版本（含激活标记与差异摘要）
  switch <id>         切换激活版本到某个老版本（重写 top250.js，不新增版本）
  diff <id>           对比某版本与当前激活版本的 all 总榜差异
  show <id>           打印某版本 all 总榜前若干条

设计要点：
  - 每个版本 = data/top250_versions/<id>.js（整份 top250.js 全文快照，含 all/分类/年榜 24 个 variant）
  - manifest = data/top250_versions/manifest.js（active 指针 + 各版本摘要；同时是 top250.html 的 JS 全局，file:// 下不能用 fetch 读 JSON 故只留这一份）
  - 老版本永不删除；switch 只改激活指针 + 重写 top250.js，不动快照
  - 只重新抓取 all 总榜（真正的“TOP250”）；y2008..y2026 与 0..3 分类是历史切片，原样保留
  - 无变动（新增=退出=名次变动 全 0）时不建议新增版本
"""
import os as _os, sys as _sys
ROOT = _os.path.dirname(_os.path.abspath(__file__))
while not _os.path.isdir(_os.path.join(ROOT, "data")) and ROOT != _os.path.dirname(ROOT):
    ROOT = _os.path.dirname(ROOT)
SR = _os.path.join(ROOT, "_script")
for _d in [SR] + [_os.path.join(SR, x) for x in _os.listdir(SR)
             if _os.path.isdir(_os.path.join(SR, x)) and x not in ("legacy", "__pycache__") and not x.startswith(".")]:
    if _d not in _sys.path:
        _sys.path.insert(0, _d)
import os, sys, re, io, json, time, datetime, subprocess, copy, argparse

import win_utf8          # noqa: F401 重定向/管道下固定 UTF-8
import build_collection_js as bc   # bump_refs 复用

SITE = os.path.join(ROOT, "data", "site")
TOP250_JS = os.path.join(SITE, "top250.js")
VERS_DIR = os.path.join(ROOT, "data", "top250_versions")
MANIFEST = os.path.join(VERS_DIR, "manifest.js")
WB = os.path.join(ROOT, "_pipeline")
DRAFT = os.path.join(WB, "top250_draft.json")
CK_PATH = os.path.join(WB, "javdb_cookie.txt")

PROXY = "http://127.0.0.1:7890"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
RANKINGS_URL = "https://javdb.com/rankings/top"
ALL_VARIANT = "all"

ITEM = re.compile(r'<div class="item[^"]*">\s*<a href="(/v/[^"]+)" class="box" title="([^"]*)">', re.S)
RANK = re.compile(r'<span class="ranking">(\d+)</span>')
CODE = re.compile(r'<strong>([^<]+)</strong>')


# ---------- 读写 ----------
def load_cookie():
    if os.path.exists(CK_PATH):
        c = io.open(CK_PATH, encoding="utf-8").read().strip()
        if c:
            return c
    return ""


def load_obj_from_text(t):
    i = t.index("window.JAVDB_RANKINGS")
    j = t.index("=", i)
    k = j + 1
    while t[k] in " \t":
        k += 1
    dec = json.JSONDecoder()
    obj, _ = dec.raw_decode(t[k:])
    return obj, t[:i]


def load_obj(path=TOP250_JS):
    return load_obj_from_text(io.open(path, encoding="utf-8").read())[0]


def read_pre(path=TOP250_JS):
    if not os.path.exists(path):
        return ("/* TOP250 排行榜：多分类/年度分片，仅结构（rank+code），影片详情统一从 movies.js "
                "按 code 取（自动生成，勿手改） */\n")
    t = io.open(path, encoding="utf-8").read()
    i = t.index("window.JAVDB_RANKINGS")
    return t[:i]


def serialize(obj, pre=None):
    if pre is None:
        pre = read_pre()
    return pre + "window.JAVDB_RANKINGS = " + json.dumps(obj, ensure_ascii=False, indent=1) + ";\n"


def write_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------- 抓取 ----------
def curl(url, cookie, timeout=25):
    cmd = ["curl", "-sL", "--ssl-no-revoke", "--max-time", str(timeout),
           "-x", PROXY, "-A", UA, "-H", "Referer: " + RANKINGS_URL, url]
    if cookie:
        cmd += ["-H", "Cookie: " + cookie]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return r.stdout or ""


def parse_html(html):
    anchors = [(m.start(), m.group(1)) for m in ITEM.finditer(html)]
    items = []
    for i, (s, _url) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(html)
        block = html[s:end]
        if 'class="ranking"' not in block:
            continue
        rk = RANK.search(block)
        cd = CODE.search(block)
        if not rk or not cd:
            continue
        items.append({"rank": int(rk.group(1)), "code": cd.group(1).strip()})
    return items


def fetch_live_all(max_pages=20):
    cookie = load_cookie()
    seen = {}
    for page in range(1, max_pages + 1):
        html = curl("%s?page=%d" % (RANKINGS_URL, page), cookie)
        if page == 1:
            if "ranking" not in html or len(html) < 3000:
                sys.stderr.write(
                    "[fetch] 第 1 页未拿到排行榜内容（len=%d）。代理已确认可达，多半是 "
                    "_pipeline/javdb_cookie.txt 过期（rankings/top 需要登录）——请先刷新 cookie。\n" % len(html))
                sys.exit(2)
        got = parse_html(html)
        if not got:
            break
        for it in got:
            seen[it["rank"]] = it
        if len(got) < 20:
            break
        time.sleep(2.0)
    return [seen[k] for k in sorted(seen) if k]


# ---------- 版本 / manifest ----------
def load_manifest():
    if not os.path.exists(MANIFEST):
        return {"active": None, "versions": []}
    t = io.open(MANIFEST, encoding="utf-8").read()
    i = t.find("window.JAVDB_TOP250_VERSIONS")
    if i < 0:
        return {"active": None, "versions": []}
    eq = t.index("=", i) + 1
    while t[eq] in " \t":
        eq += 1
    try:
        obj, _ = json.JSONDecoder().raw_decode(t[eq:])   # 跳过赋值符，直接解析内嵌 JSON（无视字符串里的 }）
    except ValueError:
        return {"active": None, "versions": []}
    return obj


def save_manifest(m):
    os.makedirs(VERS_DIR, exist_ok=True)
    # 单一清单文件：既是 Python 存储格式，又是 top250.html 的 JS 全局（file:// 下不能用 fetch 读 JSON）
    js = ("// TOP250 版本清单（自动生成，勿手改）。由 _script/crawl/top250_update.py 维护。\n"
          "window.JAVDB_TOP250_VERSIONS = " + json.dumps(m, ensure_ascii=False, indent=1) + ";\n")
    with io.open(MANIFEST, "w", encoding="utf-8") as f:
        f.write(js)


def bump_manifest_ref(version=None):
    """刷新 top250.html 里 data/top250_versions/manifest.js 的 ?v= 版本号（与 bump_refs 同理防缓存）。"""
    version = version or datetime.date.today().strftime("%Y%m%d")
    p = os.path.join(ROOT, "top250.html")
    if not os.path.isfile(p):
        return
    t = io.open(p, encoding="utf-8").read()
    t2 = re.sub(r'(data/top250_versions/manifest\.js)(?:\?v=\d{8})?', r'\1?v=' + version, t)
    if t2 != t:
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(t2)


def active_obj():
    m = load_manifest()
    if m["active"]:
        return load_obj(os.path.join(VERS_DIR, m["active"] + ".js"))
    return load_obj(TOP250_JS)[0]


def all_items_of(obj):
    return obj["top250"]["variants"][ALL_VARIANT]["items"]


def diff_all(old_items, new_items):
    old_map = {it["code"]: it["rank"] for it in old_items}
    new_map = {it["code"]: it["rank"] for it in new_items}
    added = [c for c in new_map if c not in old_map]
    dropped = [c for c in old_map if c not in new_map]
    shifted = [c for c in new_map if c in old_map and new_map[c] != old_map[c]]
    unchanged = [c for c in new_map if c in old_map and new_map[c] == old_map[c]]
    return added, dropped, shifted, unchanged


def snap_path(vid):
    return os.path.join(VERS_DIR, vid + ".js")


# ---------- 子命令 ----------
def cmd_init():
    obj = load_obj()
    m = load_manifest()
    if m["active"]:
        print("已有激活版本 %s，init 跳过（如需重建基线请先手动删除 manifest）" % m["active"])
        return
    updated = obj["top250"]["variants"][ALL_VARIANT].get("updated", "") or "orig"
    vid = "baseline_" + updated.replace("-", "")
    content = serialize(obj)
    write_file(snap_path(vid), content)
    total = len(all_items_of(obj))
    m["versions"].append({"id": vid, "created": _now(), "updated_label": updated,
                          "source": "baseline",
                          "summary": {"total": total, "added": 0, "dropped": 0,
                                      "shifted": 0, "unchanged": total}})
    m["active"] = vid
    save_manifest(m)
    print("基线版本 %s 已建立（TOP250 总榜 %d 条，整份含全部 variant 已快照）" % (vid, total))


def cmd_check():
    new_items = fetch_live_all()
    old_obj = active_obj()
    old_items = all_items_of(old_obj)
    old_map = {it["code"]: it["rank"] for it in old_items}
    new_map = {it["code"]: it["rank"] for it in new_items}
    added, dropped, shifted, unchanged = diff_all(old_items, new_items)
    with io.open(DRAFT, "w", encoding="utf-8") as f:
        json.dump(new_items, f, ensure_ascii=False, indent=1)
    m = load_manifest()
    total_changes = len(added) + len(dropped) + len(shifted)
    print("=" * 60)
    print("TOP250 增量检查（实时抓取自 %s）" % RANKINGS_URL)
    print("激活版本 : %s" % (m["active"] or "（当前 top250.js，尚未登记基线）"))
    print("实时总榜条数 : %d" % len(new_items))
    print("-" * 60)
    print("★ 新增进入 TOP250 : %d" % len(added))
    if added:
        print("   ", ", ".join("%d:%s" % (new_map[c], c) for c in added[:40]))
    print("退出 TOP250     : %d" % len(dropped))
    if dropped:
        print("   ", ", ".join("%d:%s" % (old_map[c], c) for c in dropped[:40]))
    print("名次变动        : %d" % len(shifted))
    if shifted:
        print("   ", ", ".join("%s %d→%d" % (c, old_map[c], new_map[c]) for c in shifted[:40]))
    print("完全未变        : %d" % len(unchanged))
    print("-" * 60)
    print("变动合计 %d 条 → %s" % (total_changes,
          "建议新增版本（运行 commit）" if total_changes else "无变动，无需新增版本"))
    print("=" * 60)


def cmd_commit():
    new_items = fetch_live_all()
    m = load_manifest()
    prev_obj = active_obj()
    prev_items = all_items_of(prev_obj)
    added, dropped, shifted, unchanged = diff_all(prev_items, new_items)
    today = datetime.date.today()
    today_iso = today.strftime("%Y-%m-%d")
    new_obj = copy.deepcopy(prev_obj)
    new_obj["top250"]["variants"][ALL_VARIANT]["items"] = new_items
    new_obj["top250"]["variants"][ALL_VARIANT]["updated"] = today_iso
    content = serialize(new_obj)
    os.makedirs(VERS_DIR, exist_ok=True)
    if not m["active"]:
        pu = prev_obj["top250"]["variants"][ALL_VARIANT].get("updated", "") or "orig"
        baseid = "baseline_" + pu.replace("-", "")
        write_file(snap_path(baseid), serialize(prev_obj))
        m["versions"].append({"id": baseid, "created": _now(), "updated_label": pu,
                              "source": "baseline",
                              "summary": {"total": len(prev_items), "added": 0, "dropped": 0,
                                          "shifted": 0, "unchanged": len(prev_items)}})
        print("（无基线，已先快照上一版 %s）" % baseid)
    vid = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    write_file(snap_path(vid), content)
    m["versions"].append({"id": vid, "created": _now(), "updated_label": today_iso,
                          "source": "fetch",
                          "summary": {"total": len(new_items), "added": len(added),
                                      "dropped": len(dropped), "shifted": len(shifted),
                                      "unchanged": len(unchanged)}})
    m["active"] = vid
    save_manifest(m)
    write_file(TOP250_JS, content)        # 与快照内容一致，直接落盘
    bc.bump_refs()
    bump_manifest_ref()
    print("已新增版本 %s 并切换为激活版本；top250.js 已重写，缓存版本号已 bump。" % vid)
    print("新增 %d / 退出 %d / 名次变动 %d（总计 %d）" %
          (len(added), len(dropped), len(shifted),
           len(added) + len(dropped) + len(shifted)))


def cmd_list():
    m = load_manifest()
    if not m["versions"]:
        print("尚无版本；先跑 init（建立基线）或 check 后 commit。")
        return
    print("版本列表（active=*）：")
    for v in m["versions"]:
        mark = " *" if v["id"] == m["active"] else "  "
        s = v.get("summary", {})
        print("%s %-22s %s  src=%-9s total=%-4s +%d/-%d/~%d" %
              (mark, v["id"], v.get("created", ""), v.get("source", ""),
               s.get("total", "?"), s.get("added", 0), s.get("dropped", 0), s.get("shifted", 0)))


def cmd_switch(vid):
    m = load_manifest()
    if not os.path.exists(snap_path(vid)):
        print("版本 %s 不存在" % vid)
        return
    content = io.open(snap_path(vid), encoding="utf-8").read()
    write_file(TOP250_JS, content)
    bc.bump_refs()
    m["active"] = vid
    save_manifest(m)
    bc.bump_refs()
    bump_manifest_ref()
    print("已切换到版本 %s（top250.js 已重写，缓存版本号已 bump）" % vid)


def cmd_diff(vid):
    m = load_manifest()
    if not os.path.exists(snap_path(vid)):
        print("版本 %s 不存在" % vid)
        return
    a_items = all_items_of(load_obj(snap_path(vid)))
    b_items = all_items_of(active_obj())
    added, dropped, shifted, unchanged = diff_all(a_items, b_items)
    print("版本 %s  vs  激活版本 %s  的 all 总榜差异：" % (vid, m["active"]))
    print("  %s 有但激活版无（退出）: %d" % (vid, len(added)))
    print("  激活版有但 %s 无（新增）: %d" % (vid, len(dropped)))
    print("  名次变动: %d" % len(shifted))


def cmd_show(vid, n=20):
    if not os.path.exists(snap_path(vid)):
        print("版本 %s 不存在" % vid)
        return
    items = all_items_of(load_obj(snap_path(vid)))[:n]
    for it in items:
        print("%3d  %s" % (it["rank"], it["code"]))


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser(description="TOP250 增量更新 + 快照版本管理")
    sub = ap.add_subparsers(dest="cmd")
    for c in ("init", "check", "commit", "list"):
        sub.add_parser(c)
    p_sw = sub.add_parser("switch"); p_sw.add_argument("id")
    p_df = sub.add_parser("diff"); p_df.add_argument("id")
    p_sh = sub.add_parser("show"); p_sh.add_argument("id")
    a = ap.parse_args()
    cmd = a.cmd or "check"
    if cmd == "init":
        cmd_init()
    elif cmd == "check":
        cmd_check()
    elif cmd == "commit":
        cmd_commit()
    elif cmd == "list":
        cmd_list()
    elif cmd == "switch":
        cmd_switch(a.id)
    elif cmd == "diff":
        cmd_diff(a.id)
    elif cmd == "show":
        cmd_show(a.id)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
