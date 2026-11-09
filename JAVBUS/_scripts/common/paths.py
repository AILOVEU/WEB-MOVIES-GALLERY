# -*- coding: utf-8 -*-
"""目录布局的单一来源（2026-11-02 整理定）。所有脚本从这里取路径，别再各自拼 ROOT。

根目录只留 index.html（画廊与帖子详情同一文件，靠 #tid= 路由）和下面这些目录：

| 目录 | 装什么 | 删了会怎样 |
|---|---|---|
| `assets/images/` | 图片字节（唯一的大头，人工整份复制到移动硬盘） | 本机破图，硬盘还有；下一轮只补新图 |
| `data/` | 交付层：`data.js`（列表）+ `posts/shard_<i>.js`（详情分片，点开某帖才注入它所在的一片） | 重跑 build.py 即可 |
| `_rawdata/` | **原始抓取件**：`list_pN.html` + `posts/<tid>_pN.html` | 要重新爬（耗时+流量+cookie），属于丢数据 |
| `_pipeline/` | **过程与状态**：`parsed/`、`index.json` 累积库、`imgmap.json` 台账、`crawl_done.txt` 断点、`last_new.txt`、`cookie.txt`、`store_state.json`、`baselines/`（guard.py 回归快照） | 丢数据/丢断点，要重爬或重算 |
| `_logs/` | 纯 informational：`*.log`、`img_fail.txt`、`guard_history.log`，验证截图放 `_logs/shots/`；**文件名强制带 `YYYYMMDD_` 日期前缀** | **随便删，不丢任何数据** |
| `_backup/` | 快照备份，**按日期建子目录** `_backup/YYYYMMDD/`（inc/backup.py 维护：index.json、imgmap.json、crawl_done.txt、data.js、index.html、store_config.json + MANIFEST） | 删了丢回退点（历史版本全没） |
| `_scripts/` | 全部 py 工具 + 配置 + 手册，已按功能分区（见下方） | 工具没了 |

`_scripts/` 分区：
  `common/`  公共库：paths.py（本文件，布局单一来源）、discuz.py（页面解析）、
             pannet.py（网盘识别）、spider_kit.py（登录站爬虫工具箱：本项目无人 import，只作参考，
             与 inc/crawl.py 是并行两套实现，改限速/判定规则要两边同步——详见 spider_kit 顶部声明）
  `inc/`     增量链：update.py（总控）、crawl.py、parse.py、images.py、guard.py
  `site/`    全站：build.py（交付层）、validate.py（交付前校验）
  `store/`   图片与硬盘策略：external_store.py、store_config.json
跨分区引用一律走 `paths.script("build.py")` 按名字定位，不要再写死相对路径。

约定：脚本一律直接写目标文件（不用 tmp/.part/os.replace），并发轮次靠目录隔离。
日志铁规（2026-11-03 定）：`_logs/` 里的文件名一律带 `YYYYMMDD_` 日期前缀（含 `shots/` 里的
验证截图）。脚本产出走下面的 `dated()` 自动加；你手工重定向请写 `> _logs/$(date +%Y%m%d)_update.log`；
没遵守的会被 `update.py` 开头拎出来提醒。
"""
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # _scripts/common
SCRIPTS = os.path.dirname(HERE)                             # _scripts
ROOT = os.path.dirname(SCRIPTS)                             # 项目根
TOOL_DIRS = ("common", "inc", "site", "store")              # _scripts 下的功能分区


def script(name):
    """按文件名在 _scripts 各分区里定位脚本/配置，返回绝对路径。

    update.py 调子脚本、external_store 读 store_config.json 都走这里，以后再挪分区不用改代码。
    找不到就报错并列出现有名字，绝不静默跑错文件。
    """
    base = os.path.basename(name)
    if os.path.isfile(os.path.join(SCRIPTS, base)):
        return os.path.abspath(os.path.join(SCRIPTS, base))
    for d in TOOL_DIRS:
        p = os.path.join(SCRIPTS, d, base)
        if os.path.isfile(p):
            return os.path.abspath(p)
    have = [f for f in os.listdir(SCRIPTS) if f.endswith((".py", ".json"))]
    for d in TOOL_DIRS:
        dd = os.path.join(SCRIPTS, d)
        if os.path.isdir(dd):
            have += ["%s/%s" % (d, f) for f in os.listdir(dd) if f.endswith((".py", ".json"))]
    raise SystemExit("[paths] 找不到 %r；_scripts 下现有：%s" % (base, ", ".join(sorted(have))))


# ---- 交付层（站点本身）
ASSETS = os.path.join(ROOT, "assets")
IMAGES = os.path.join(ASSETS, "images")            # 图片字节
IMG_REL = "assets/images"                          # 台账/交付层里写的 posix 相对前缀
AV_REL = "assets/avatars"                          # 头像（目前不抓取，留着备用）
DATA = os.path.join(ROOT, "data")                  # data.js + posts/
DATA_POSTS = os.path.join(DATA, "posts")          # 详情层：分片快照，一帖一行 window.THREADS["<tid>"]={...}
DATA_INDEX = os.path.join(DATA, "data.js")
# 详情层分片数（单一来源，build.py 写、validate.py 校、index.html 只从 data.js 的 window.SHARD_N 读，不写死）。
# tid 整除取模落到 shard_<i>.js，把"一帖一文件"（曾 714 个碎文件）压成固定少量大文件，
# 拷贝到移动硬盘更快、资源管理器不卡，同时保留"点开才加载"（点某帖只注入它所在那一片）。
SHARD_N = 16


def shard_path(tid, n=SHARD_N):
    """tid -> 所属分片文件绝对路径。tid 非数字（脏数据）一律落第 0 片，绝不抛异常打断 build。"""
    try:
        i = int(str(tid)) % n
    except (TypeError, ValueError):
        i = 0
    return os.path.join(DATA_POSTS, "shard_%d.js" % (i if 0 <= i < n else i % n))


def shard_of(tid, n=SHARD_N):
    """tid -> 分片序号（与前端 index.html 里 parseInt(tid)%N 同口径，非数字归 0）。"""
    try:
        i = int(str(tid)) % n
    except (TypeError, ValueError):
        i = 0
    return i if 0 <= i < n else i % n

# ---- 原始抓取件（不可再生）
RAW = os.path.join(ROOT, "_rawdata")
RAW_POSTS = os.path.join(RAW, "posts")             # 帖子页原始 HTML，与交付层 data/posts/*.js 无关

# ---- 过程与状态（删了丢数据）
WORK = os.path.join(ROOT, "_pipeline")   # 流水线中间区：台账/断点/派生 JSON/cookie 都在这
PARSED = os.path.join(WORK, "parsed")
COOKIE = os.path.join(WORK, "cookie.txt")
IMGMAP = os.path.join(WORK, "imgmap.json")         # 永久台账：URL -> assets/images/...
INDEX_JSON = os.path.join(WORK, "index.json")      # 累积库（新帖插最前）
# 注：原先这里有 IMAGES_JSON（_pipeline/images.json 下载清单），但 images.py 早已改成直接扫
# parsed/，那个文件写了没人读，2026-11-03 从 parse.py 停产。
LAST_NEW = os.path.join(WORK, "last_new.txt")      # 本轮新增 tid，跨步骤传递
PENDING_REFS = os.path.join(WORK, "pending_refs.txt")   # refs 逐层闭包的收尾记录（未收敛时下轮续抓）
CRAWL_DONE = os.path.join(WORK, "crawl_done.txt")  # 抓取断点
STORE_STATE = os.path.join(WORK, "store_state.json")
BASELINES = os.path.join(WORK, "baselines")       # guard.py 的回归快照（pre / post 两份）

# ---- 日志区（可随便删）
LOGS = os.path.join(ROOT, "_logs")
SHOTS = os.path.join(LOGS, "shots")                # 验证截图
LOG_DATE_RE = re.compile(r"^\d{8}_")               # 日志文件名强制的 8 位日期前缀


def dated(name, day=None):
    """给日志/截图文件名强制上 `YYYYMMDD_` 前缀（已有前缀则不动）。
    同一天多次运行共用一份：img_fail 这类是覆盖写，guard_history 这类是追加写。"""
    base = name.replace("\\", "/").split("/")[-1]
    if LOG_DATE_RE.match(base):
        return base
    return "%s_%s" % (day or time.strftime("%Y%m%d"), base)


IMG_FAIL = os.path.join(LOGS, dated("img_fail.txt"))        # 失败清单（当日一份，下次跑重写）
GUARD_LOG = os.path.join(LOGS, dated("guard_history.log"))  # 每轮 guard diff 结论流水，按日分文件


def logs_violations():
    """_logs/（含 shots/）里没带日期前缀的文件 -> 相对路径清单。update.py 每轮开头提醒。"""
    out = []
    for dirpath, _dirs, files in os.walk(LOGS):
        for f in files:
            if not LOG_DATE_RE.match(f):
                out.append(os.path.relpath(os.path.join(dirpath, f), ROOT).replace("\\", "/"))
    return sorted(out)


# ---- 快照备份区（按日期分目录，由 _scripts/inc/backup.py 维护）
BACKUP = os.path.join(ROOT, "_backup")


def backup_dir(day=None):
    """今日（或指定日）的备份目录：_backup/YYYYMMDD/。"""
    return os.path.join(BACKUP, day or time.strftime("%Y%m%d"))


def ensure(*paths):
    """按需建目录（幂等）。写盘前调用，避免重定向/落盘因为目录不存在而失败。"""
    for p in paths:
        if p:
            os.makedirs(p, exist_ok=True)
    return paths


# 只要 import 了本模块，这几个目录就一定在：_pipeline/ 是状态与台账的家；_logs/ 和
# _logs/shots/ 是日志与验证截图的去处；_backup/ 由 inc/backup.py 按日期写快照。
# 注意 shell 的重定向是在 python 启动前开文件的，
# 所以如果你刚把 _logs/ 整个删掉，得先 `mkdir -p _logs`（或先跑一次不带重定向的脚本）。
# assets/images 不在这里建（归 write-guard 与 images.py 的落盘逻辑管），
# data/ 归 build.py 管，_rawdata/ 归 crawl.py 管。
ensure(WORK, LOGS, SHOTS, BACKUP)
