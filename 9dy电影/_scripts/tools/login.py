# -*- coding: utf-8 -*-
"""登录 9dy.top, 保存 Cookie 到 <dir>/_pipeline/<今日>_cookies_clean.txt 并验证。

过程文件按日期留档（每次登录一份，不互相覆盖）；_scripts/pipeline/06_magnets.py 会自动取最新的一份。

用法:
  python _scripts/tools/login.py <用户名或邮箱> <密码>
  python _scripts/tools/login.py <用户名或邮箱> <密码> --dir=<画廊目录>

机制: AJAX action=zongcai_login; 成功后通过下载弹窗的免费次数提示或
      wp-admin 重定向判定登录态; Cookie 存为单行 header 供其他脚本使用。
"""
import json, os, re, sys, time
import http.cookiejar
import urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -> _scripts/（common.py 所在）
from common import gallery_dir, pipeline_file, UA, CTX, BASE_URL

GDIR = gallery_dir()
COOKIE_OUT = pipeline_file(GDIR, "cookies_clean.txt")     # _pipeline/YYYY-MM-DD_cookies_clean.txt

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(jar),
    urllib.request.HTTPSHandler(context=CTX))


def req(url, data=None, headers=None):
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if headers:
        h.update(headers)
    r = urllib.request.Request(url, data=data, headers=h)
    return opener.open(r, timeout=30).read().decode("utf-8", errors="replace")


_args = [a for a in sys.argv[1:] if not a.startswith("--")]      # 旗标与位置参数可乱序
if len(_args) < 2:
    raise SystemExit("用法: python _scripts/tools/login.py <用户名或邮箱> <密码> [--dir=<画廊目录>]")
user, pwd = _args[0], _args[1]

# 1. 首页拿基础 cookie
home = req(BASE_URL + "/")
print("home ok, cookies:", len(jar))

# 2. AJAX 登录
body = urllib.parse.urlencode({
    "action": "zongcai_login",
    "username": user,
    "password": pwd,
}).encode()
resp = req(BASE_URL + "/wp-admin/admin-ajax.php", data=body, headers={
    "X-Requested-With": "XMLHttpRequest",
    "Referer": BASE_URL + "/",
    "Content-Type": "application/x-www-form-urlencoded",
})
print("login resp:", resp[:200])

# 3. 验证: 拉 21164 的下载弹窗, 看免费次数提示 (REST 需 nonce 会误报, 不用)
try:
    modal = req(BASE_URL + "/wp-admin/admin-ajax.php?action=ceo_shop_pay_product&product_id=21164",
                headers={"X-Requested-With": "XMLHttpRequest",
                         "Referer": BASE_URL + "/21164.html"})
except Exception as e:
    modal = str(e)
quota = re.search(r"今日免费次数剩余\s*(\d+)\s*次", modal)
print("modal quota hint:", quota.group(0) if quota else "(no quota text, len=%d)" % len(modal))

if (quota and int(quota.group(1)) > 0) or ("购买" in modal and quota):
    parts = ["%s=%s" % (c.name, c.value) for c in jar]
    with open(COOKIE_OUT, "w", encoding="utf-8") as f:
        f.write("; ".join(parts) + "\n")
    print("SAVED", len(parts), "cookies ->", COOKIE_OUT)
    print("names:", [c.name for c in jar])
else:
    # 兜底: 用 wp-admin 重定向判定 (未登录会 302 到 wp-login.php)
    try:
        r = opener.open(urllib.request.Request(
            BASE_URL + "/wp-admin/profile.php", headers={"User-Agent": UA}), timeout=30)
        final = r.geturl()
    except Exception as e:
        final = getattr(e, "url", "")
    print("wp-admin final url:", final)
    if "wp-login" not in final:
        parts = ["%s=%s" % (c.name, c.value) for c in jar]
        with open(COOKIE_OUT, "w", encoding="utf-8") as f:
            f.write("; ".join(parts) + "\n")
        print("SAVED", len(parts), "cookies ->", COOKIE_OUT)
    else:
        print("LOGIN FAILED — 不写入 cookie 文件")
        sys.exit(1)
