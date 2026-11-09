#!/bin/bash
# 总控：等当前『all』这一轮跑完 -> 补跑 all -> 依次 y2026..y2020（每个两遍兜回偶发失败）
# 已下过的番号靠 videos_manifest.json + trailers_ckpt.json 自动跳过，不会重下。
cd "d:/workspace/z_WEB-MOVIES-GALLERY/JAVDB" || exit 1
#export PYTHONIOENCODING=utf-8
mkdir -p _logs _pipeline/hlstmp
PY=python
D="_script/crawl/trailers_download.py"
DATE=$(date +%F)
LOG="_logs/${DATE}_master_run.log"
exec >> "$LOG" 2>&1
echo "===================== master start $(date '+%F %T') ====================="

# 1) 等待正在运行的『all』首轮结束（日志出现完成标记；glob 匹配任意日期）
while ! grep -q '完成：成功' _logs/*trailers_run.log 2>/dev/null; do
  sleep 20
done
echo "[all] 首轮完成，执行补跑 pass"
$PY -u "$D" --workers 4 --variant all

# 2) 年份榜：2026 -> 2020，每个两遍（第二遍只处理偶发失败，成功的会秒跳过）
for v in y2026 y2025 y2024 y2023 y2022 y2021 y2020; do
  echo "--------------------- [$v] pass1 $(date '+%T') ---------------------"
  $PY -u "$D" --workers 4 --variant "$v"
  echo "--------------------- [$v] pass2 (retry) $(date '+%T') ---------------------"
  $PY -u "$D" --workers 4 --variant "$v"
done

echo "===================== master done $(date '+%F %T') ====================="
