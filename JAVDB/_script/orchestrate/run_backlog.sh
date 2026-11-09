#!/usr/bin/env bash
# 欠账清剿总控（后台长跑用）。顺序设计要点：
#   · 同一时刻只允许一个进程写 movies.js（covers_check / merge_collection / media_scan 串行或错开）
#   · 收藏爬虫只写 ckpt/actors.js/series.js，可与封面下载并行（封面走直连，不抢代理）
#   · 磁力只写 magnets.js + 自己的断点，可与二轮封面并行
#   · 任何爬虫 rc=2 = cookie 失效 → 立刻整体中止（重抽 cookie 后再重跑本脚本，全部可续传）
# 用法：  nohup bash _script/run_backlog.sh > "_logs/$(date +%F)_backlog_nohup.log" 2>&1 &
#   只跑部分阶段：MAG_ALL=0 bash _script/run_backlog.sh   （MAG_ALL=0 时磁力只补看过/想看，不扫全库）
# 日志：  均强制带日期前缀 _logs/<YYYY-MM-DD>_xxx.log：backlog_master / _spider / _covers / _covers2 /
#         _merge / _mag / _final（阶段间跳日时以最新日期那份为准）
set -u
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-python}
CVW=${CVW:-8}                       # 封面并发（直连 CDN，实测 8 稳定）
MGW=${MGW:-2}                       # 磁力并发（走代理，别贪高，见 RESUME 第 5 节坑 2）
MAG_ALL=${MAG_ALL:-1}               # 1=连全库缺磁力的都扫；0=只补看过/想看
MAG_CAP=${MAG_CAP:-40}              # 每部最多保留多少条磁力（控制 magnets.js 体积）
mkdir -p _logs _pipeline/hlstmp
DATE=$(date +%F)
M=_logs/${DATE}_backlog_master.log
say(){ echo "[$(date '+%m-%d %H:%M:%S')] $*" | tee -a "$M"; }

run_spider(){   # 监管循环：机器休眠/偶发崩溃能自动重启，直到 rc=0；rc=2 视为 cookie 失效立刻停
  local tag="$1"; shift
  while true; do
    "$@" >> "_logs/${DATE}_${tag}.log" 2>&1
    local rc=$?
    if [ $rc -eq 0 ]; then say "$tag DONE"; return 0; fi
    if [ $rc -eq 2 ]; then say "$tag 中止：cookie 失效（按 RESUME 第 5 节坑 3 重抽后重跑本脚本）"; return 2; fi
    say "$tag 异常退出 rc=$rc，10s 后重启"
    sleep 10
  done
}

covers(){ "$PY" _script/manifest/covers_check.py --repair --workers "$CVW" >> "$1" 2>&1; say "封面补齐一轮结束（$(tail -1 "$1" | cut -c1-90)）"; }

say "===== 阶段 1：收藏补拓（62 演員 + 121 系列）‖ 封面全量补齐（直连 $CVW 并发）====="
run_spider backlog_spider "$PY" _script/crawl/collection_spider.py &
SP=$!
( covers _logs/${DATE}_backlog_covers.log ) &
CV=$!
rc_cv=0; rc_sp=0
wait $CV || rc_cv=$?
wait $SP || rc_sp=$?
if [ $rc_sp -eq 2 ]; then say "阶段 1 中止：cookie 失效"; exit 2; fi
say "阶段 1 完成（spider rc=$rc_sp, covers rc=$rc_cv）"

say "===== 阶段 2：合并 ckpt -> movies.js，重建清单配置，同步播放字段 ====="
SKIP_COVERS=1 "$PY" _script/build/merge_collection.py >> _logs/${DATE}_backlog_merge.log 2>&1 && say "merge_collection OK"
"$PY" _script/build/build_collection_js.py >> _logs/${DATE}_backlog_merge.log 2>&1 && say "build_collection_js OK"
"$PY" _script/manifest/media_scan.py >> _logs/${DATE}_backlog_merge.log 2>&1 && say "media_scan OK"

say "===== 阶段 3：磁力（先看过/想看，MAG_ALL=$MAG_ALL 时再扫全库）‖ 新片封面 ====="
( covers _logs/${DATE}_backlog_covers2.log ) &
CV2=$!
run_spider backlog_mag "$PY" _script/crawl/magnet_spider.py --scope watched --workers "$MGW" --cap "$MAG_CAP"
run_spider backlog_mag "$PY" _script/crawl/magnet_spider.py --scope want   --workers "$MGW" --cap "$MAG_CAP"
if [ "$MAG_ALL" = "1" ]; then
  run_spider backlog_mag "$PY" _script/crawl/magnet_spider.py --scope missing --workers "$MGW" --cap "$MAG_CAP"
else
  say "MAG_ALL=0：跳过全库磁力扫描（以后可单独跑 magnet_spider.py --scope missing）"
fi
wait $CV2

say "===== 阶段 4：收尾对账 ====="
"$PY" _script/manifest/media_scan.py >> _logs/${DATE}_backlog_final.log 2>&1
"$PY" _script/manifest/videos_manifest.py scan >> _logs/${DATE}_backlog_final.log 2>&1
"$PY" _script/manifest/covers_check.py >> _logs/${DATE}_backlog_final.log 2>&1
say "ALL DONE —— 各日志尾部确认：backlog_mag / backlog_covers2 / backlog_final"
