# -*- coding: utf-8 -*-
"""批量获取影片磁力链接 -> <dir>/_rawdata/magnets.json

机制:
  首轮  GET /wp-admin/admin-ajax.php?action=ceo_shop_pay_product&product_id={id}
        返回下载弹窗 HTML, 从中提取 magnet:?xt= 链接 (direct 类无需登录也行,
        但统一带上 Cookie 更稳)。
  二次点击 (--click2) 对「购买弹窗且 0 金币」的影片调
        action=ceo_shop_pay_product_download 消耗每日免费次数换取磁力,
        成功的记录 via=click2 (画廊标记「二次点击获取」)。

用法:
  python _scripts/pipeline/06_magnets.py --test=15384            # 单片调试, 打印原始响应
  python _scripts/pipeline/06_magnets.py                         # 首轮全量 (断点续传, 只扫 magnets.json 里没有的)
  python _scripts/pipeline/06_magnets.py --ids=1,2,3             # 只扫指定 id (已存在默认跳过)
  python _scripts/pipeline/06_magnets.py --ids-file=x.txt --force   # 指定 id 强制重扫
  python _scripts/pipeline/06_magnets.py --click2                # 首轮后对 0 金币影片执行二次点击
  python _scripts/pipeline/06_magnets.py --refetch-no-magnet     # 无磁力影片批量重抓 (VIP/empty/error/other 全部重试)
  python _scripts/pipeline/06_magnets.py --refetch-no-magnet --dry-run   # 只列出重抓目标, 不请求
  python _scripts/pipeline/06_magnets.py --dir=<画廊目录>
  python _scripts/pipeline/06_magnets.py --log-dir=<响应快照目录>   # 默认 <dir>/_logs/magnets-resp（可随意删）

需要登录态的命令 (--click2 / --refetch-no-magnet) 前先跑 _scripts/tools/login.py。
账号未登录或免费次数耗尽会连续失败 3 次自动中止, 不会空烧次数。
"""
import json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> _scripts/（common.py 所在）
from common import (gallery_dir, latest_pipeline_file, log_subdir, dated_name,
                    meta_file, get, parse_ids_arg, load_datajs, sleep_polite, has_flag,
                    arg_val, atomic_write_json, BASE_URL)

GDIR = gallery_dir()
RAW = meta_file(GDIR, "_raw_posts.json")
OUT = meta_file(GDIR, "magnets.json")
COOKIE = latest_pipeline_file(GDIR, "cookies_clean.txt")   # 取 _pipeline/ 下最新一份（文件名带日期前缀）
# 原始响应快照只用于调试, 默认写 _logs/magnets-resp（可随时删除）; 可用 --log-dir= 改位置
RESP_DIR = arg_val("--log-dir=") or log_subdir(GDIR, "magnets-resp")
os.makedirs(RESP_DIR, exist_ok=True)

DRY = has_flag("--dry-run")
FORCE = has_flag("--force")
REFETCH = has_flag("--refetch-no-magnet")

COOKIE_STR = ""
if COOKIE:
    with open(COOKIE, encoding="utf-8") as f:
        COOKIE_STR = f.read().strip()
if not COOKIE:
    print("警告: _pipeline/ 下没有 cookies_clean.txt — 先跑 _scripts/tools/login.py（仅 direct 类磁力可获取）")
elif not COOKIE_STR or COOKIE_STR.startswith("<!"):
    print("警告: Cookie 为空/无效（%s）— 仅 direct 类磁力可获取" % os.path.basename(COOKIE))

with open(RAW, encoding="utf-8") as f:
    ids = [it["id"] for it in json.load(f)["items"]]


def get_modal(product_id):
    """下载弹窗 (ceo_shop_pay_product) -> (html, err)"""
    url = ("%s/wp-admin/admin-ajax.php?action=ceo_shop_pay_product"
           "&product_id=%d" % (BASE_URL, product_id))
    return get(url, headers={
        "Cookie": COOKIE_STR,
        "Referer": "%s/%d.html" % (BASE_URL, product_id),
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*; q=0.01",
    })


def get_download(product_id):
    """二次点击接口 (消耗免费次数) -> (text, err)"""
    url = ("%s/wp-admin/admin-ajax.php?action=ceo_shop_pay_product_download"
           "&product_id=%d&sku=1" % (BASE_URL, product_id))
    return get(url, headers={
        "Cookie": COOKIE_STR,
        "Referer": "%s/%d.html" % (BASE_URL, product_id),
        "X-Requested-With": "XMLHttpRequest",
    })


def parse(html):
    """从下载弹窗 HTML 提取磁力/网盘/提示, 并判定状态"""
    magnets = re.findall(r"magnet:\?xt=urn:btih:[A-Za-z0-9&=%.]+", html)
    magnets = [m.replace("&amp;", "&") for m in magnets]
    pans = re.findall(r"https?://(?:pan\.baidu|www\.alicloud|www\.alipan|cloud\.189|115|caiyun|quark)[^\s\"'<>]+", html)
    notice = ""
    nm = re.search(r"(请先登录|开通会员|余额不足|仅限VIP购买|次数不足|今日免费次数剩余\s*\d+\s*次)[^<]{0,40}", html)
    if nm:
        notice = nm.group(1)
    if magnets:
        state = "direct"
    elif "购买" in html and ("支付" in html or "VIP" in html):
        state = "purchase"       # 需登录(可能需VIP)后走 download 流程
    elif len(html) < 50:
        state = "empty"
    else:
        state = "other"
    return sorted(set(magnets)), sorted(set(pans)), notice, state


def save(result):
    atomic_write_json(OUT, result, indent=1)   # 原子写: magnets.json 就是断点续传进度，不能写坏


def phase1(result, todo):
    """首轮扫描: 弹窗 HTML 提取 (低并发 + 礼貌延时)"""
    print("phase1: %d products to scan" % len(todo), flush=True)
    workers = int(os.environ.get("WORKERS", "2"))
    for a in sys.argv[1:]:
        if a.startswith("--workers="):
            workers = int(a.split("=")[1])

    def work(pid):
        html, err = get_modal(pid)
        sleep_polite(0.8)
        if err:
            return pid, {"error": err, "state": "error"}
        m, p, notice, state = parse(html)
        return pid, {"magnets": m, "pans": p, "notice": notice,
                     "state": state, "raw_len": len(html)}

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(work, pid): pid for pid in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            pid, rec = fut.result()
            result[str(pid)] = rec
            if n % 20 == 0 or n == len(todo):
                save(result)
                nm = sum(1 for v in result.values() if v.get("magnets"))
                print("progress %d/%d 有磁力=%d rate=%.1f/s"
                      % (n, len(todo), nm, n / (time.time() - t0)), flush=True)


def phase2_click2(result, allowed=None, max_consec_fail=3):
    """对 purchase/unknown/error 态执行二次点击 (消耗每日免费次数!)

    仅当弹窗为 0 金币(免费)时点击; 磁力标记 via=click2。
    allowed 不为空时只点这些 id（避免 --ids/--refetch 把全库老记录一并消耗次数）。
    连续 max_consec_fail 次失败(次数不足/未登录)则中止。
    """
    todo = [int(k) for k, v in result.items()
            if v.get("state") in ("purchase", "unknown", "error") and not v.get("magnets")
            and (allowed is None or int(k) in allowed)]
    if allowed is not None:
        print("phase2: 已按目标白名单收窄 (%d 个候选 id)" % len(allowed), flush=True)
    print("phase2: %d products to try second-click" % len(todo), flush=True)
    ok = skip = fail = consec_fail = 0
    for n, pid in enumerate(todo, 1):
        if consec_fail >= max_consec_fail:
            print("phase2: 中止 — 连续 %d 次失败(次数不足或未登录)" % consec_fail, flush=True)
            break
        # 1) 拉购买弹窗, 校验 0 金币 (非0金币跳过不算失败)
        html, err = get_modal(pid)
        if err:
            consec_fail += 1
            fail += 1
            continue
        if not re.search(r"(单价|支付金额)[^0-9]*0\.00", html):
            result[str(pid)]["notice"] = "click2: 非0金币/需VIP, 跳过"
            result[str(pid)]["state"] = "vip"
            skip += 1
            continue
        consec_fail = 0
        # 2) 二次点击 download 接口
        dresp, derr = get_download(pid)
        sleep_polite(1.0)
        if derr or dresp is None:
            result[str(pid)]["notice"] = "click2: " + derr
            fail += 1
            consec_fail += 1
            continue
        if dresp.lstrip().startswith("{"):
            try:
                j = json.loads(dresp)
                msg = str(j.get("data", ""))[:60]
            except Exception:
                msg = dresp[:60]
            result[str(pid)]["notice"] = "click2: " + msg
            fail += 1
            consec_fail += 1
            print("phase2 %d: %s" % (pid, msg), flush=True)
            if "未登录" in msg:
                print("phase2: Cookie 失效(未登录), 中止 — 请重跑 _scripts/tools/login.py", flush=True)
                break
            continue
        # 3) HTML 响应: 直接提取磁力
        m = sorted(set(x.replace("&amp;", "&") for x in
                       re.findall(r"magnet:\?xt=urn:btih:[A-Za-z0-9&=%.]+", dresp)))
        if m:
            result[str(pid)] = {"magnets": m, "pans": [], "state": "direct",
                                "via": "click2", "raw_len": len(dresp)}
            ok += 1
            consec_fail = 0
        else:
            result[str(pid)]["notice"] = "click2: 响应无磁力"
            fail += 1
        if n % 20 == 0:
            save(result)
            print("phase2 progress %d/%d ok=%d skip=%d fail=%d"
                  % (n, len(todo), ok, skip, fail), flush=True)
    save(result)
    print("phase2 DONE ok=%d skip=%d fail=%d" % (ok, skip, fail), flush=True)


def main():
    test = arg_val("--test=")
    if test:
        test = int(test)
        html, err = get_modal(test)
        if err:
            print("ERROR:", err)
            return
        path = os.path.join(RESP_DIR, dated_name("%d_test.html" % test))   # 日志名强制带日期前缀
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        m, p, notice, state = parse(html)
        print("response saved:", path, len(html), "bytes")
        print("magnets:", m)
        print("pans:", p[:3])
        print("notice:", notice, "| state:", state)
        return

    result = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            result = json.load(f)

    # ---- 目标集合 ----
    click2_allowed = None        # 二次点击范围: None=全库（仅 --click2 不带 --ids 时）
    if REFETCH:
        # 无磁力影片批量重抓: magnets.json 里 state != direct 且无磁力的记录,
        # 删除旧记录后重扫 + 二次点击
        states = arg_val("--states=", "vip,empty,error,other,unknown")
        allow = set(states.split(","))
        targets = [int(k) for k, v in result.items()
                   if not v.get("magnets") and v.get("state", "unknown") in allow]
        if arg_val("--ids=") or arg_val("--ids-file="):
            only = set(parse_ids_arg() or [])
            targets = [t for t in targets if t in only]
        print("refetch-no-magnet targets:", len(targets))
        from collections import Counter
        c = Counter(result[str(t)].get("state") for t in targets)
        print("  by state:", dict(c))
        if DRY:
            print("(dry-run, 未做任何修改)")
            return
        for t in targets:
            result.pop(str(t), None)
        todo = targets
        click2_allowed = set(targets)      # 重抓哪批就只二次点击哪批，不空烧全库次数
    else:
        only = parse_ids_arg()
        scan_ids = only if only else ids
        todo = [i for i in scan_ids if FORCE or str(i) not in result]
        if only:
            click2_allowed = set(only)

    print("total ids:", len(ids), "| todo:", len(todo))
    if todo and not DRY:
        phase1(result, todo)
        save(result)
        nm = sum(1 for v in result.values() if v.get("magnets"))
        print("phase1 DONE 有磁力: %d / %d" % (nm, len(result)), flush=True)

    if has_flag("--click2") or REFETCH:
        if DRY:
            print("(dry-run: 跳过二次点击)")
        else:
            phase2_click2(result, click2_allowed)


if __name__ == "__main__":
    main()
