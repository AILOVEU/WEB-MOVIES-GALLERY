#!/bin/bash
# 收尾扫描：等主任务(master_run.log 出现 'master done')后，用低并发(2)对各榜单再兜一遍，
# 专治并发争抢代理导致的偶发 key_fail/seg_fail。已完成的会被清单/断点秒跳过。
cd "d:/workspace/z_WEB-MOVIES-GALLERY/JAVDB" || exit 1
#export PYTHONIOENCODING=utf-8
mkdir -p _logs _pipeline/hlstmp
PY=python
D="_script/crawl/trailers_download.py"
DATE=$(date +%F)
LOG="_logs/${DATE}_sweep_run.log"
exec >> "$LOG" 2>&1
echo "===================== sweep start $(date '+%F %T') ====================="

# 等主任务结束（glob 匹配任意日期的 master_run）
while ! grep -q 'master done' _logs/*master_run.log 2>/dev/null; do
  sleep 30
done
echo "主任务已结束，开始低并发收尾 ..."

for v in all y2026 y2025 y2024 y2023 y2022 y2021 y2020; do
  echo "--------------------- [sweep $v] $(date '+%T') ---------------------"
  $PY -u "$D" --workers 2 --retries 3 --variant "$v"
done

echo "===================== sweep done $(date '+%F %T') ====================="
