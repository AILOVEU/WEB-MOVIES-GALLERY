#!/usr/bin/env bash
# 统计并行分段的聚合吞吐与进度
# [own] 分类=download 自写脚本（tg_video_dl 下载器，原属 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁出仓库），非 Telegram 上游
OUT="D:/workspace/z_WEB-MOVIES-GALLERY/JAVDB/assets/水果派"
cd "$OUT" || exit 1
sum() {
  t=0
  for f in *.part.p*; do [ -f "$f" ] && t=$(( t + $(stat -c %s "$f") )); done
  for f in *.part; do [ -f "$f" ] && t=$(( t + $(stat -c %s "$f") )); done
  echo "$t"
}
a=$(sum); t0=$(date +%s)
sleep "${1:-60}"
b=$(sum); t1=$(date +%s)
d=$(( t1 - t0 ))
kb=$(( (b - a) / 1024 ))
echo "窗口 ${d}s：新增 $(( kb / 1024 )) MiB -> 聚合 $(( kb / d )) KB/s"
echo "--- 各段明细 ---"
ls -la --time-style=+%H:%M:%S | grep -E "\.part" | awk '{printf "  %-26s %8.1f MiB  %s\n", $NF, $5/1048576, $6}'
echo "--- 已完成 ---"
ls *.mp4 2>/dev/null | sed 's/^/  /' || echo "  (无)"
