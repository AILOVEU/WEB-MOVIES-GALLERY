# -*- coding: utf-8 -*-
"""
rebuild.py — 一键重建 / 数据体检

四级口径（默认最轻，逐级加严）:
  _scripts\\py.cmd _scripts\\build\\rebuild.py            # --site ：从 parsed + images 重建离线站（s06 + _verify）
  _scripts\\py.cmd _scripts\\build\\rebuild.py --parse    # 从 HTML 缓存重新解析，再重建站点
  _scripts\\py.cmd _scripts\\build\\rebuild.py --all      # 全链路：s01→s02→s03→s04i→s04l→s04f→s04d→s06→_verify
  _scripts\\py.cmd _scripts\\build\\rebuild.py --check    # 只体检，不写任何文件

设计要点（踩过的坑，勿改）:
1. 子脚本一律用探测出的解释器调起：_scripts\\py.cmd 可能选用回退解释器，必须与当前解释器保持一致。
   步骤脚本路径写的是相对 _scripts/ 的（如 build/s06_build_site.py），由 C.script_path() 拼绝对。
2. 子进程输出写入 _logs/<日期>/rebuild_<step>.log（字节流），控制台只回显尾部；
   直接捕获并 print UTF-8 中文在 GBK 控制台会 UnicodeEncodeError 中断流水线（§1.37）。
3. 顺序不可换：s04f 必须在 s04d 之前，s04d 会重填 images[].local（§1.35）。
4. 所有步骤幂等，不删任何既有文件：s02/s03/s04i 都是「已完成则跳过」，天然增量。
"""
import os
import re
import sys
import json
import time
import subprocess

sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
import common as C

URLS = os.path.join(C.SRC, "urls.json")
MAN = os.path.join(C.SRC, "image_manifest.json")

VENV_PY = r"C:\Users\wh\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
ALT_PY = r"C:\Users\wh\.workbuddy-ai\binaries\python\versions\3.13.12\python.exe"
SP = r"C:\Users\wh\.workbuddy\binaries\python\envs\default\Lib\site-packages"

# 步骤表里的路径都是相对 _scripts/ 的（改目录分组只改这里）
SITE = [("s06", "build/s06_build_site.py"), ("verify", "audit/_verify.py")]
PARSE = [("s03", "parse/s03_parse.py")] + SITE
ALL = [
    ("s01", "collect/s01_collect_urls.py"),
    ("s01b", "collect/s01b_collect_pages.py"),
    ("s02", "collect/s02_fetch_html.py"),
    ("s02b", "collect/s02b_fetch_pagenav.py"),
    ("s03", "parse/s03_parse.py"),
    ("s04i", "images/s04i_fetch_body_fast.py"),
    ("s04l", "images/s04l_proxy_unwrap.py"),
    ("s04f", "images/s04f_rebuild_manifest.py"),
    ("s04d", "images/s04d_rewrite.py"),
    ("s06", "build/s06_build_site.py"),
    ("verify", "audit/_verify.py"),
]


_PY = None
_ENV = None


def pick_python():
    """选一个真能 import 依赖的解释器，返回 (python, env)。

    WorkBuddy 更新期间 vendor/python 目录会被反复抽空，不能假设 sys.executable 可用；
    第一个候选失败就回退到同版本完整解释器 + 原 venv 的 site-packages（§1.38）。
    """
    global _PY, _ENV
    if _PY and os.path.exists(_PY):
        return _PY, _ENV
    for py, sp in ((VENV_PY, ""), (ALT_PY, SP)):
        if not py or not os.path.exists(py):
            continue
        env = dict(os.environ)
        if sp:
            env["PYTHONPATH"] = sp
        try:
            r = subprocess.run([py, "-c", "import encodings,requests,PIL,lxml"],
                               env=env, capture_output=True, timeout=120)
        except Exception:
            continue
        if r.returncode == 0:
            _PY, _ENV = py, env
            return _PY, _ENV
    _PY, _ENV = sys.executable, dict(os.environ)
    return _PY, _ENV


# ---------------------------------------------------------------- 本轮范围（增量与归档重抓共用）
# 这些步骤的开销本来与**库规模**成正比（逐篇开容器）；传 --keys-file 后改成与
# **本轮批大小**成正比。update.py（增量）与 archive.py（移出归档重抓）共用这一套，
# 所以定义放在流水线层。**不传范围路径就是全树**（rebuild.py --all 走的就是这一档）。
SCOPE_STEPS = ("s02", "s02b", "s03", "s04i", "s04f", "s04d")


def touched_years(scope_path):
    """本轮范围内的文章落在哪几年 → s06 只需重建这些年；返回 None = 别收敛。

    宁肯多花几十秒全树重建，也不能把新年份的文章漏掉（漏了分片就是整年打不开的页面）。
    """
    got = C.scope_read(scope_path)
    if not got:
        return None
    slots = set()
    for u in got:
        slot, _p = C.Y.locate(C.url_key(u))
        if not slot or not (len(slot) == 4 and slot.isdigit()):
            return None
        slots.add(slot)
    return sorted(slots)


def step_extra(name, scope_path, yflag=None):
    """按步骤名拼命令行（步骤都是子进程，只能靠参数传范围）。

    返回 [] 就是「不给任何限定」，子进程自然走全树历史行为。
    s06 按年收敛（分片按年聚合，p 序号也按年算；按篇收敛只会漏掉整年的分片重排）；
    verify 不收敛：它是范围模式的回归门。
    """
    if not scope_path:
        return list(yflag) if (name == "s06" and yflag) else []
    if name in SCOPE_STEPS:
        return ["--keys-file=" + scope_path]
    if name == "s06":
        yrs = touched_years(scope_path)
        if yrs:
            return ["--years=" + ",".join(yrs)]
        return list(yflag or [])
    return []


def run_step(name, script, tail=6, extra=None):
    """执行一个流水线步骤，返回 (rc, 秒, 日志尾部行)。

    子进程解释器取自 pick_python() 的探测结果，不用 sys.executable：
    即使父进程能跑，子进程的 venv 目录也可能在同一时刻被抽空（§1.38）。
    extra 传子脚本的命令行参数（如 ["--quick"]）。
    """
    logp = C.log_path("rebuild_%s.log" % name)      # 本轮日志：_logs/<今天>/
    py, env = pick_python()
    t0 = time.time()
    with open(logp, "wb") as f:
        p = subprocess.run(
            [py, "-u", C.script_path(script)] + list(extra or []),
            cwd=C.ROOT, stdout=f, stderr=subprocess.STDOUT, env=env,
        )
    dt = time.time() - t0
    lines = []
    try:
        size = os.path.getsize(logp)
        with open(logp, "rb") as f:
            f.seek(max(0, size - 6000))
            txt = f.read().decode("utf-8", "replace")
        lines = [l.rstrip() for l in txt.splitlines() if l.strip()][-tail:]
    except OSError:
        pass
    return p.returncode, dt, lines


def count_files(top, suffix="", min_size=0):
    n = 0
    for dp, dn, fn in os.walk(top):
        for f in fn:
            if suffix and not f.endswith(suffix):
                continue
            try:
                if os.path.getsize(os.path.join(dp, f)) > min_size:
                    n += 1
            except OSError:
                pass
    return n


def counts():
    """全量计数（体检与校验报告共用）。

    年度层：原料一篇一容器（_pipeline/material/<年>/<key>.jsonl），文章页聚合成分片 data/<年>/part<i>.html；
    统计只读每个容器的 meta 行（不吞 1GB 正文），图片那遇顺带查异常文件。
    """
    c = {}
    c["urls"] = len(C.read_json(URLS, {}) or {})
    st = C.Y.stats()
    c["material"] = st["containers"]
    c["html"] = st["with_raw"]
    c["parsed"] = st["with_parsed"]
    c["subpages"] = st["subpages"]
    c["by_year"] = st["by_year"]
    c["year_slots"] = len(st["by_year"])
    c["manifest"] = len(C.read_json(MAN, {}) or {})
    # 图片计数走 store 解析后的路径；库在本机之外且未挂载时记 None（未挂载），
    # 避免把「拔盘」误报成「图片全丢」的 -14 万差异。
    c["store_state"], _cfg, _detail = C.store_state()
    n = zero = badname = 0
    if os.path.isdir(C.IMAGES_DIR):
        for dp, dn, fn in os.walk(C.IMAGES_DIR):
            for f in fn:
                p = os.path.join(dp, f)
                try:
                    s = os.path.getsize(p)
                except OSError:
                    continue
                n += 1
                if s == 0:
                    zero += 1
                if len(os.path.splitext(f)[0]) != 20:
                    badname += 1
    else:
        n = zero = badname = None
    c["images"] = n
    c["img_zero"] = zero
    c["img_badname"] = badname
    c["articles"] = C.Y.pages_total()
    c["datajs"] = year_data_entries()
    # 年度 `_audit.json` 对账：只比 16 个小文件就能报“哪年少了几页”
    c["audit"] = C.Y.audit_mismatches()
    c["audit_slots"] = len([s for s in C.Y.existing_slots()
                            if os.path.exists(C.Y.audit_path(s))])
    return c


def year_data_entries():
    """各年 data.js 里的条目总数（列表页实际能看到多少篇）。拿不到返回 None。"""
    total = 0
    for p in C.Y.year_data_files():
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
            total += len(json.loads(txt[txt.index("["):txt.rindex("]") + 1]))
        except (OSError, ValueError):
            return None
    return total


def _fmt(v):
    return "未挂载" if v is None else v


def write_check_report(c, path):
    rows = [
        ("图片库位置(store)", c.get("store_state", "local")),
        ("sitemap/urls.json", c["urls"]),
        ("年度容器(原料)", c["material"]),
        ("  ├ 含原始 HTML", c["html"]),
        ("  ├ 含解析产物", c["parsed"]),
        ("  └ 分页子页", c["subpages"]),
        ("年份个数", c["year_slots"]),
        ("images 文件", _fmt(c["images"])),
        ("  ├ 0 字节", _fmt(c["img_zero"])),
        ("  └ 非 20 位命名", _fmt(c["img_badname"])),
        ("manifest 条目", c["manifest"]),
        ("文章页(分片锚点 data/<年>/part<i>.html)", c["articles"]),
        ("年度 data.js 条目", c["datajs"] if c["datajs"] is not None else -1),
        ("年度 _audit 对账", ("%d 年一致" % c.get("audit_slots", 0))
         if not c.get("audit") else "%d 处不符" % len(c["audit"])),
    ]
    lines = ["=" * 62, "FULIBA 数据体检  %s" % time.strftime("%Y-%m-%d %H:%M:%S"), "=" * 62]
    for k, v in rows:
        lines.append("%-22s %10s" % (k, v))
    if c.get("store_state") == "incremental":
        lines.append("%-22s %10s" % ("  └ 口径", "本机只存增量新图，历史图在别的电脑"))
    # 一致性判定（只提示，不阻断）
    diff = []
    for k in ("material", "parsed", "articles"):
        if c[k] != c["urls"]:
            diff.append("%s=%d vs urls=%d" % (k, c[k], c["urls"]))
    if c["datajs"] not in (None, c["parsed"]):
        diff.append("data.js 条目=%s vs parsed=%d" % (c["datajs"], c["parsed"]))
    for slot, msg in c.get("audit") or []:
        diff.append("%s/%s" % (slot, msg))
    lines.append("-" * 62)
    lines.append("结论: %s" % ("计数一致" if not diff else "存在差异（仅供参考）：" + "；".join(diff)))
    txt = "\n".join(lines) + "\n"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)


def main():
    mode = "site"
    if "--check" in sys.argv:
        mode = "check"
    elif "--all" in sys.argv:
        mode = "all"
    elif "--parse" in sys.argv:
        mode = "parse"

    py, _env = pick_python()
    st, scfg, sdetail = C.store_state()
    print("[rebuild] 模式=%s  Python=%s" % (mode, py))
    print("[rebuild] 图片库 state=%s mode=%s target=%s ｜ %s"
          % (st, scfg["mode"], scfg.get("external_root"), sdetail))
    if mode == "check":
        c = counts()
        write_check_report(c, C.log_path("check_report.txt"))
        print("CHECK_OK")
        return 0

    steps = ALL if mode == "all" else (PARSE if mode == "parse" else SITE)
    if st in ("detached", "broken"):
        hit = [s for s in steps if s[0] in ("s04i", "s04l", "s04f", "s04d", "s04r", "s04j", "s04m")]
        if hit:
            print("[rebuild] 图片库未挂载 -> %s 会被闸门拦下并记为「跳过」，"
                  "其余步骤照常（网站仍可打开，新文章图片待插盘后补）" % "/".join(s[0] for s in hit))
    elif st == "incremental":
        print("[rebuild] 图片库 incremental -> 图片步骤只处理增量新图"
              "（manifest 有条目的历史图不重下、台账不清、历史引用不改）")
    t0 = time.time()
    fails = []
    skipped = []
    for name, script in steps:
        print("[rebuild] >>> %-7s %-26s" % (name, script))
        rc, dt, lines = run_step(name, script)
        for l in lines:
            print("    " + l)
        print("[rebuild] <<< %-7s rc=%d  %.1fs" % (name, rc, dt))
        if C.ST.image_steps_skipped(rc):
            skipped.append(name)
            continue
        if rc != 0:
            fails.append((name, rc))
    total = time.time() - t0

    c = counts()
    write_check_report(c, C.log_path("check_report.txt"))
    print("[rebuild] 总耗时 %.1fs" % total)
    if skipped:
        print("[rebuild] 图片库闸门跳过：%s" % "/".join(skipped))
    if fails:
        print("REBUILD_FAIL " + " ".join("%s=%d" % f for f in fails))
        return 1
    print("REBUILD_OK %d" % int(total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
