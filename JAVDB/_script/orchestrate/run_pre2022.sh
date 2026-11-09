#!/bin/bash
# 继续下载「2022 年之前」的预告片：y2021 -> y2008（14 个年份榜）。
# 已下过的番号靠 根目录 videos_manifest.json + trailers_ckpt.json 自动跳过，绝不重下
# （即使 videos/ 为空也认清单，见 RESUME.md 第 5.5/7 节）。
# 策略：每个年份 pass1(workers=4)+pass2(workers=4) 兜回偶发瞬断，最后对整个区间做一遍
#       workers=3 低并发收尾扫描。同一时刻只有一个 python 顺序跑各榜，避免多进程抢代理。
#       注：实测 workers=6 会把单节点代理打满(120s 仅完成 1 条、单条~113s)，故并发上限取 4。
cd "d:/workspace/z_WEB-MOVIES-GALLERY/JAVDB" || exit 1
#export PYTHONIOENCODING=utf-8
mkdir -p _logs _pipeline/hlstmp
PY=python
D="_script/crawl/trailers_download.py"
DATE=$(date +%F)
LOG="_logs/${DATE}_pre2022_run.log"
exec >> "$LOG" 2>&1
YEARS="y2021 y2020 y2019 y2018 y2017 y2016 y2015 y2014 y2013 y2012 y2011 y2010 y2009 y2008"

echo "===================== pre-2022 start $(date '+%F %T') ====================="
for v in $YEARS; do
  echo "--------------------- [$v] pass1 $(date '+%T') ---------------------"
  $PY -u "$D" --workers 3 --retries 3 --variant "$v"
  echo "--------------------- [$v] pass2 (retry) $(date '+%T') ---------------------"
  $PY -u "$D" --workers 3 --retries 3 --variant "$v"
done

echo "===================== low-concurrency sweep $(date '+%T') ====================="
for v in $YEARS; do
  echo "--------------------- [sweep $v] $(date '+%T') ---------------------"
  $PY -u "$D" --workers 3 --retries 3 --variant "$v"
done

echo "===================== pre-2022 done $(date '+%F %T') ====================="
