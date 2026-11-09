#!/bin/bash
# 启动一个「走 7890 代理 + 开 CDP 调试端口 9222 + 隔离 profile」的可视化 Chrome，打开 JavDB 登录页。
# 用途：让 Cloudflare 在这个当前代理 IP 下发新的 cf_clearance，随后用 refresh_cookie.js / dump_cookies.js 抓取。
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # 脚本在 _script/cookie/ 下，上溯两级=仓库根
CHROME="/c/Program Files/Google/Chrome/Application/chrome.exe"
PROFILE="$ROOT/_pipeline/jdb_chrome"   # 持久 profile（登录态 + CF 凭证都养在这里，删了要重新过质询）
URL="https://javdb.com/users/sign_in"
[ -d "$PROFILE" ] || echo "[提示] profile 不存在，将新建（= 要重新过 Cloudflare 验证 + 登录）：$PROFILE"
"$CHROME" \
  --remote-debugging-port=9222 \
  --user-data-dir="$PROFILE" \
  --proxy-server="http://127.0.0.1:7890" \
  --no-first-run --no-default-browser-check \
  --new-window "$URL" >/dev/null 2>&1 &
echo "chrome launched pid $!  (调试端口 9222, 代理 7890, profile _pipeline/jdb_chrome)"
