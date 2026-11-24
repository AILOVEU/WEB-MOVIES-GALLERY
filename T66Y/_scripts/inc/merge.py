# -*- coding: utf-8 -*-
"""把 _rawdata 里本轮抓到的页合并进累积库 _pipeline/records.json。

流程（crawl.py 之后跑）：
  1) 读累积库（不存在则从 _rawdata 现有页初始化）
  2) 扫 _rawdata 下所有页，解析出记录
  3) 按 tid 合并：新 tid 插入，旧 tid 用新数据覆盖（回复/下载/最后回复会变）
  4) 重算水位线 = 全库里 post_ts 最大的非置顶帖
  5) 落盘

为什么按 tid 而不是按页：
  列表按发帖时间降序，新帖插最前、旧帖整体后移。按页覆盖会让旧页尾部丢帖，
  按 tid 累积则天然幂等 —— 同一帖被多页抓到只保留一份，页码在 build 时重排。

用法：
  python _scripts/inc/merge.py            # 合并 + 更新水位线
  python _scripts/inc/merge.py --dry      # 只看会新增/更新多少，不写盘
  python _scripts/inc/merge.py --rebuild  # 忽略现有库，从 _rawdata 全量重建
"""
import argparse
import io
import os
import sys

_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_SCRIPTS, "common"))
import paths as P
import store as S
import t66y as T


def log(*a):
    print("[merge]", *a, flush=True)


def scan_raw(fid):
    """扫 _rawdata 下该 fid 的所有页，返回 [(page, [recs])]，按页码升序。"""
    out = []
    if not os.path.isdir(P.RAW):
        return out
    pat = "fid%d_p" % fid
    for fn in sorted(os.listdir(P.RAW)):
        if not (fn.startswith(pat) and fn.endswith(".html")):
            continue
        m = fn[len(pat):-5]
        if not m.isdigit():
            continue
        page = int(m)
        html = io.open(os.path.join(P.RAW, fn), encoding="utf-8", errors="ignore").read()
        recs = T.parse_list_page(html, page)
        if recs:
            out.append((page, recs))
    out.sort(key=lambda x: x[0])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fid", type=int, default=25)
    ap.add_argument("--dry", action="store_true", help="只统计，不写盘")
    ap.add_argument("--rebuild", action="store_true",
                    help="忽略现有 records.json，从 _rawdata 全量重建")
    a = ap.parse_args()

    pages = scan_raw(a.fid)
    if not pages:
        log("_rawdata 下没有 fid=%d 的可用页，先跑 crawl.py" % a.fid)
        return 1

    if a.rebuild or not os.path.exists(P.RECORDS):
        data, dup = S.init_from_raw(a.fid)
        log("初始化累积库：%d 条（_rawdata 去重丢 %d）" % (len(data["records"]), dup))
        if a.dry:
            log("(dry-run，未写盘)")
            return 0
        S.save(a.fid, data)
        st = S.stats(data)
        log("已保存 → %s" % P.RECORDS)
        log("水位线：tid=%s（%s）" % (st["watermark_tid"], st["watermark_seen"]))
        return 0

    data = S.load(a.fid)
    before = len(data["records"])
    old_wm = (data.get("watermark") or {}).get("tid") or ""

    added = updated = 0
    for page, recs in pages:
        a_n, u_n = S.merge(data, recs)
        added += a_n
        updated += u_n

    # 水位线：全库最新的一条（不只看本轮，避免某轮只抓到旧页时把水位线退回去）
    data["watermark"] = S.pick_watermark(data["records"].values())

    after = len(data["records"])
    log("本轮扫描 %d 页 / %d 条记录" % (len(pages), sum(len(r) for _, r in pages)))
    log("合并结果：新增 %d，更新 %d，库 %d → %d 条" % (added, updated, before, after))

    new_wm = data["watermark"].get("tid") or ""
    if new_wm and new_wm != old_wm:
        log("水位线推进：%s → %s（%s）"
            % (old_wm or "(无)", new_wm, data["watermark"].get("seen") or ""))
    else:
        log("水位线未变：%s" % (new_wm or "(无)"))

    if a.dry:
        log("(dry-run，未写盘)")
        return 0

    size = S.save(a.fid, data)
    log("已保存 → %s（%.1f MB）" % (P.RECORDS, size / 1024.0 / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
