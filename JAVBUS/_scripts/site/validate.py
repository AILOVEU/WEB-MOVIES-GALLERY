# -*- coding: utf-8 -*-
"""交付前校验（本机落盘 + 人工外迁版）：
  1) data/data.js 与 data/posts/*.js 引用的每条本地图片路径，逐层判定在哪：
       LOCAL     本机 assets/images/ 下有真实字节（本轮刚下的、或还没被删走的）
       JUNCTION  本机可达，但 assets/images 是旧策略留下的目录联接
       HDD       本机看不到，落在 store_config.json 绑定的硬盘站里（盘插着时）
       ARCHIVED  本机看不到，且没有绑定的硬盘站，但 _pipeline/imgmap.json 台账里有这条路径
                 = 这张图曾经成功落盘、之后随"整份复制到移动硬盘 + 删本机 assets/images/"被搬走了
                 -> 预期状态，判可接受（拔盘浏览旧文章破图由前端 onerror 隐藏）
       GONE      台账里没有 + 两处都看不到 -> 唯一会 FAIL 的情况（说明记录与磁盘不一致）
  2) 本轮新增帖（_pipeline/last_new.txt）的图片单独复核：本机可见 = 本轮已落盘且还没被你复制走；
     本机看不到但台账在案 = 你已做完人工收尾（整份复制走 + 删本机图片目录），同样判可接受；
     两处都没有才是本轮真没落盘 -> FAIL。
  3) 统计残留远程内容图（forum.javcdn.cc 未被本地化的，应为 0；表情/失败允许）
  4) 交付层 JS 语法：一个 node 进程用 vm.Script 批量编译 data/data.js 与每个 data/posts/*.js
     （节点缺失或批量不可用时自动回落逐文件 `node --check`）
用法： python _scripts/site/validate.py   （需先 build.py）
"""
import glob
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # _scripts
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))                      # 公共库：paths.py
import paths as P

ROOT = P.ROOT
CFG_PATH = P.script("store_config.json")
IMGMAP_PATH = P.IMGMAP                 # 过程区的永久台账
DATA_DIR = P.DATA
LAST_NEW_PATH = P.LAST_NEW             # 过程区：本轮新增 tid
IMG_LEDGER_PREFIX = P.IMG_REL          # 本地图片引用的路径前缀（旧布局是 images/）

try:  # Windows GBK 控制台无法编码中文/表情，强制 stdout 为 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def load_store_cfg():
    try:
        return json.load(io.open(CFG_PATH, encoding="utf-8"))
    except Exception:
        return {}


def store_ctx():
    """返回 (硬盘站根目录 or None, 图片目录是否联接点, 联接是否可达, 图片目录相对路径)。"""
    cfg = load_store_cfg()
    site = None
    if cfg.get("hdd_root"):
        site = os.path.normpath(os.path.join(cfg["hdd_root"], cfg.get("hdd_project_subdir", "site")))
    img = cfg.get("scope", {}).get("images_dir", IMG_LEDGER_PREFIX)
    d = os.path.join(ROOT, img)
    try:
        st = os.lstat(d)
        is_j = bool(getattr(st, "st_file_attributes", 0) & 0x400)
    except OSError:
        is_j = False
    reach = os.path.isdir(d) and any(True for _ in glob.iglob(os.path.join(d, "*", "*")))
    return site, is_j, reach, img


SHARD_ASSIGN = 'window.THREADS["'   # build.py 写详情分片的每帖行前缀


def load_shard_threads(posts_dir):
    """解析 data/posts/shard_*.js -> (tid->thread 映射, [(分片路径, 原文), ...])。

    分片是一帖一行 `window.THREADS["<tid>"] = {json};`，json.dumps 单行不换行，故按物理行解析；
    只认行首前缀，正文里偶含同名字符串也在 JSON 串内（引号被转义），不会误判成赋值行。
    原文一并返回，供 remote_content（forum.javcdn 残留）等整片字符串统计复用。
    """
    threads = {}
    raws = []
    for path in sorted(glob.glob(os.path.join(posts_dir, "*.js"))):
        raw = io.open(path, encoding="utf-8").read()
        raws.append((path, raw))
        # 严格按物理换行 \n 切，绝不用 splitlines()：json.dumps(ensure_ascii=False) 会把正文里的
        # \n/\r 转义成两字符，故一帖一赋值行内不含裸换行；但正文可能含 U+2028/U+2029/U+0085，
        # splitlines() 会在这些字符上误断行，把一行 JSON 拆成"未闭合字符串"而丢帖（实测漏 1 帖）。
        for line in raw.split("\n"):
            if not line.startswith(SHARD_ASSIGN):
                continue
            sep = line.find('"] = ', len(SHARD_ASSIGN))
            if sep < 0:
                continue
            tid = line[len(SHARD_ASSIGN):sep]
            try:
                threads[tid] = json.loads(line[sep + 5:].rstrip().rstrip(";"))
            except ValueError as e:
                print("   SHARD PARSE FAIL %s: %s" % (os.path.basename(path), e))
    return threads, raws


def is_image(path):
    if not os.path.exists(path):
        return False
    try:
        with open(path, "rb") as f:
            h = f.read(16)
    except Exception:
        return False
    return (h[:3] == b"\xff\xd8\xff" or h[:8] == b"\x89PNG\r\n\x1a\n"
            or h[:6] in (b"GIF87a", b"GIF89a") or (h[:4] == b"RIFF" and h[8:12] == b"WEBP")
            or h[:2] == b"BM")


JS_BATCH_CHECK = r"""
const fs=require('fs'),vm=require('vm');
const list=fs.readFileSync(process.argv[1],'utf8').trim().split(/\r?\n/);
let bad=0;
for(const f of list){
  try{ new vm.Script(fs.readFileSync(f,'utf8'),{filename:f}); }
  catch(e){ bad++; console.log('JSFAIL|'+f+'|'+String(e.message).split('\n')[0]); }
}
console.log('JSDONE|'+list.length+'|'+bad);
"""


def js_check_all(paths):
    """一次 node 进程批量做语法检查。

    原来是逐文件 `node --check`：426 个 data + 425 个 posts 就是 851 次 node 冷启动，
    实测要 18 分钟。这里把文件清单写成临时列表，一个 node 进程用 vm.Script 编译全部
    （等价 --check 的语法解析，不执行代码），并保留逐文件回落通道：
    任何一步不对（node 缺失/无哨兵输出）就返回 None，由调用方走原来的老逻辑。
    返回 [(路径, 错因), ...] 或 None（不可用）。
    """
    lst = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("\n".join(paths))
            lst = f.name
        r = subprocess.run(["node", "-e", JS_BATCH_CHECK, lst], capture_output=True,
                           text=True, encoding="utf-8", errors="ignore", timeout=900)
        if r.returncode != 0 or not r.stdout or "JSDONE|" not in r.stdout:
            return None
        bad = []
        for line in r.stdout.splitlines():
            if line.startswith("JSFAIL|"):
                _, p, msg = (line.split("|") + ["", ""])[:3]
                bad.append((p, msg))
        return bad
    except Exception:
        return None
    finally:
        if lst:
            try:
                os.remove(lst)
            except OSError:
                pass


def main():
    t0 = time.time()
    dj = os.path.join(DATA_DIR, "data.js")
    if not os.path.exists(dj):
        print("data/data.js 不存在，请先 build.py"); sys.exit(1)
    idx_raw = io.open(dj, encoding="utf-8").read()
    i = idx_raw.find("[")
    j = idx_raw.rfind("]")
    index = json.loads(idx_raw[i:j + 1])
    print("[validate] FORUM_INDEX 条目:", len(index))

    site, is_junction, j_reach, img = store_ctx()
    refs = set()
    occurrences = 0
    remote_content = 0
    threads, shard_raws = load_shard_threads(P.DATA_POSTS)
    tjs = [p for p, _ in shard_raws]          # node 语法检查仍逐分片文件（分片也是合法 .js）
    refs_by_tid = {}
    for it in index:
        if it.get("cover"):
            refs.add(it["cover"])
            occurrences += 1
            refs_by_tid.setdefault(it["tid"], set()).add(it["cover"])
    for _path, raw in shard_raws:
        remote_content += raw.count("forum.javcdn.cc")   # 整片字符串统计残留远程内容图
    for tid, t in threads.items():
        traw = json.dumps(t, ensure_ascii=False)
        r_local = set(re.findall(r'src="((?:assets/)?images/[^"]+)"', traw)) | set(t.get("all_images", []))
        occurrences += len(r_local)
        refs |= r_local
        refs_by_tid.setdefault(tid, set()).update(r_local)

    # imgmap 台账 = "这条本地路径曾经成功落盘"的永久记录。本机字节看不见而台账有记录，
    # 就是用户已经把整份 JAVBUS 复制到移动硬盘并删掉本机 assets/images/ 的结果，属预期。
    try:
        ledger = set(json.load(io.open(IMGMAP_PATH, encoding="utf-8")).values())
    except Exception:
        ledger = set()
    tiers = {"local": 0, "junction": 0, "hdd": 0, "archived": 0, "gone": []}
    for r in sorted(refs):
        if r.startswith("http"):
            continue
        here = is_image(os.path.join(ROOT, r))
        if here and is_junction:
            tiers["junction"] += 1
        elif here:
            tiers["local"] += 1
        elif site and is_image(os.path.join(site, *r.split("/"))):
            tiers["hdd"] += 1
        elif r in ledger:
            tiers["archived"] += 1
        else:
            tiers["gone"].append(r)
    seen_n = tiers["local"] + tiers["junction"] + tiers["hdd"] + tiers["archived"] + len(tiers["gone"])
    print("[validate] 详情分片文件:", len(tjs), "片 / 帖:", len(threads))
    print("[validate] 检查本地图片引用:", occurrences, "去重后:", len(refs), "（imgmap 台账 %d 条）" % len(ledger))
    print("[validate] 图片字节分层: 引用去重 %d -> 本机 %d / 经联接点在硬盘 %d / 仅在硬盘 %d / "
          "已人工外迁(本机已删,台账在) %d / 两处都无 %d"
          % (seen_n, tiers["local"], tiers["junction"], tiers["hdd"], tiers["archived"], len(tiers["gone"])))

    # 本轮新增帖的图应当已经在本机落盘。但 last_new.txt 是上一轮留下的清单：如果你已经按收尾流程
    # 把整份复制到移动硬盘、删了本机图片目录，这些字节本机看不到就是正常状态 —— 只要 imgmap 台账
    # 在案就归到"已外迁"，不拦。只有既不在本机又不在台账（= 当时根本没下成功）才判 FAIL。
    newp = LAST_NEW_PATH
    # 本轮新增清单：默认读 last_new.txt；但 refs 逐层闭包时 crawl 内部会多次 parse，
    # last_new 只剩最后一层（实测 149 帖只记到 6）2026-11-03 加 --new= 透传全量。
    arg = next((x.split("=", 1)[1] for x in sys.argv[1:] if x.startswith("--new=")), "")
    if arg and os.path.exists(arg):
        new_tids = [x.strip() for x in io.open(arg, encoding="utf-8")
                    if x.strip() and not x.lstrip().startswith("#")]
    elif arg:
        new_tids = [x for x in re.split(r"[,;\s]+", arg) if x]
    else:
        new_tids = [x.strip() for x in io.open(newp, encoding="utf-8").read().split("\n") if x.strip()] \
            if os.path.exists(newp) else []
    if new_tids:
        nrefs = nlocal = nnled = 0
        bad = []
        for tid in new_tids:
            for r in refs_by_tid.get(tid, ()):
                nrefs += 1
                if is_image(os.path.join(ROOT, r)):
                    nlocal += 1
                elif r in ledger:
                    nnled += 1
                else:
                    bad.append("%s:%s" % (tid, r))
        print("[validate] 本轮新增 %d 帖%s：本地图片引用 %d 条，本机可见 %d 条 / 已随整份复制外迁(台账在) %d 条%s"
              % (len(new_tids), "（来自 --new=）" if arg else "（来自 last_new.txt）", nrefs, nlocal, nnled,
                 "  <- 未落盘: " + ", ".join(bad[:8]) if bad else ""))
        new_miss = bad
    else:
        new_miss = []
    print("[validate] 残留 forum.javcdn 内容图引用(应0):", remote_content)
    # 网盘打标一致性：data.js 是展示层，posts/shard_*.js 是详情层，两层的 pan 口径必须一致，
    # 否则列表标了、点进去没标（或反过来）。统计同时给覆盖率，方便肉眼确认规则没整批漏判。
    pan_list = [x for x in index if x.get("pan") or x.get("pan_floor")]
    mismatch = []
    for it in index:
        t = threads.get(str(it["tid"]))
        if not t:                          # 该帖无详情分片条目（未抓详情），跳过一致性比对
            continue
        if sorted(x["key"] for x in (t.get("pan") or [])) != sorted(x["key"] for x in (it.get("pan") or [])):
            mismatch.append(it["tid"])
        elif sorted(x["key"] for x in (t.get("pan_floor") or [])) != sorted(x["key"] for x in (it.get("pan_floor") or [])):
            mismatch.append(it["tid"])
    stat = {}
    for it in pan_list:
        for h in (it.get("pan") or []):
            stat[h["label"]] = stat.get(h["label"], 0) + 1
        for h in (it.get("pan_floor") or []):
            stat["[楼]" + h["label"]] = stat.get("[楼]" + h["label"], 0) + 1
    print("[validate] 网盘标记: 含网盘 %d 帖（正文 %d / 仅楼层 %d），两层不一致 %d"
          % (len(pan_list), sum(1 for x in index if x.get("pan")),
             sum(1 for x in index if x.get("pan_floor") and not x.get("pan")), len(mismatch)))
    print("[validate] 网盘分布:", " ".join("%s=%d" % kv for kv in sorted(stat.items(), key=lambda x: -x[1])))
    if mismatch:
        for m in mismatch[:10]:
            print("   PAN MISMATCH", m)
    if is_junction and not j_reach:
        print("[validate] 提示: %s 是指向 %s 的联接，当前不可达（拔盘状态）-> 旧文章破图属可接受，站点仍可打开"
              % (img, site or "?"))
    missing = tiers["gone"] + new_miss
    if missing:
        for m in missing[:20]:
            print("   MISSING", m)

    # 交付层 JS 语法：一个 node 进程批量编完（失败则回落逐文件 node --check）
    js_files = [dj] + tjs
    tjs_t = time.time()
    bad = js_check_all(js_files)
    js_bad = js_ok = 0
    if bad is None:
        mode = "per-file"
        for path in js_files:
            r = subprocess.run(["node", "--check", path], capture_output=True, text=True,
                               encoding="utf-8", errors="ignore")
            if r.returncode == 0:
                js_ok += 1
            else:
                js_bad += 1
                print("   JS SYNTAX FAIL", os.path.basename(path), r.stderr[:200])
    else:
        mode = "batch"
        js_bad = len(bad)
        js_ok = len(js_files) - js_bad
        for p, msg in bad[:20]:
            print("   JS SYNTAX FAIL", os.path.basename(p), msg[:200])
        if js_bad > 20:
            print("   ...共 %d 个语法错误" % js_bad)
    print("[validate] node 语法检查(%s): OK=%d BAD=%d  用时 %.1fs" % (mode, js_ok, js_bad, time.time() - tjs_t))

    ok = (not missing) and (js_bad == 0)
    print("[validate] conclusion:", "PASS" if ok else "FAIL", "（总用时 %.1f 分）" % ((time.time() - t0) / 60.0))
    if ok and tiers["archived"]:
        print("[validate] 说明: %d 张旧图本机已不可见，但 imgmap 台账在案 -> 已随整份复制转移到移动硬盘，"
              "本机删掉了 assets/images/（你定的收尾步骤）。本机打开站点正文/评论/楼层照常，旧图由前端 onerror 隐藏；"
              "插上硬盘打开硬盘里那份即可看到全量图片" % tiers["archived"])
    if ok and tiers["hdd"] + tiers["junction"]:
        print("[validate] 说明: 图片字节在移动硬盘（外迁策略），本机打开站点正文/评论/楼层全部正常，"
              "拔盘时图片由前端 onerror 隐藏 —— 属预期")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
