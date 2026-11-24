# -*- coding: utf-8 -*-
"""T66Y 目录布局的单一来源。所有脚本从这里取路径，别再各自拼 ROOT。

根目录只留 index.html 和下面这些目录：

| 目录 | 装什么 | 删了会怎样 |
|---|---|---|
| `data/` | 交付层：`index.js`（清单+标签统计）+ `shard_XXX.js`（帖子分片） | 重跑 build.py 即可 |
| `_rawdata/` | **原始抓取件**：`list_pN.html` | 要重新爬（耗时+流量+cookie），属于丢数据 |
| `_pipeline/` | **过程与状态**：`cookie.txt`、`crawl_done.txt` 断点、`records.json` 累积库 | 丢断点，要重爬 |
| `_logs/YYYY-MM-DD/` | 纯 informational：`*.log`；截图放 `_logs/shots/YYYY-MM-DD/` | **随便删，不丢任何数据** |
| `_bak/YYYY-MM-DD/` | 快照备份（build.py 覆盖 data/ 前自动留一份） | 删了丢回退点 |
| `_scripts/` | 全部 py 工具 + 配置 | 工具没了 |

`_scripts/` 分区：
  `common/`  公共库：paths.py（本文件）、t66y.py（页面解析 + 打标签）
  `inc/`     抓取链：crawl.py
  `site/`    交付层：build.py
  `config/`  标签规则 tags.json（人工可改，改完重跑 build.py 即生效）
"""
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # _scripts/common
SCRIPTS = os.path.dirname(HERE)                             # _scripts
ROOT = os.path.dirname(SCRIPTS)                             # 项目根
TOOL_DIRS = ("common", "inc", "site", "config")

# ---- 交付层（站点本身）
DATA = os.path.join(ROOT, "data")
DATA_INDEX = os.path.join(DATA, "index.js")
DATA_SHARDS = DATA                              # shard_XXX.js 直接放 data/ 下

# 每片装多少页。50 页 x 100 帖 = 5000 条/片，约 0.9 MB，前端按需注入。
# 全库 4957 页 -> 100 片。改这个值必须重跑 build.py（index.js 里会写死，前端照读）。
PAGES_PER_SHARD = 50


def shard_path(i):
    return os.path.join(DATA_SHARDS, "shard_%03d.js" % i)


def shard_of_page(page, per=PAGES_PER_SHARD):
    """页码(1-based) -> 分片序号。"""
    return (max(1, int(page)) - 1) // per


# ---- 原始抓取件（不可再生）
RAW = os.path.join(ROOT, "_rawdata")

# ---- 过程与状态（删了丢数据）
WORK = os.path.join(ROOT, "_pipeline")
COOKIE = os.path.join(WORK, "cookie.txt")
CRAWL_DONE = os.path.join(WORK, "crawl_done.txt")     # 已抓页码断点
RECORDS = os.path.join(WORK, "records.json")          # 累积库：解析后的全部记录

# ---- 配置
CONFIG = os.path.join(SCRIPTS, "config")
TAGS_JSON = os.path.join(CONFIG, "tags.json")

# ---- 日志区（可随便删）
LOGS = os.path.join(ROOT, "_logs")
SHOTS = os.path.join(LOGS, "shots")

# ---- 快照备份区
BACKUP = os.path.join(ROOT, "_bak")


def today():
    return time.strftime("%Y-%m-%d")


def log_dir(day=None):
    """_logs/YYYY-MM-DD/ —— 按日分目录（全局规则）。"""
    return os.path.join(LOGS, day or today())


def shot_dir(day=None):
    """_logs/shots/YYYY-MM-DD/ —— 截图按日分目录。"""
    return os.path.join(SHOTS, day or today())


def backup_dir(day=None):
    """_bak/YYYY-MM-DD/ —— 备份按日分目录。"""
    return os.path.join(BACKUP, day or today())


def ensure(*paths):
    """按需建目录（幂等）。写盘前调用，避免因为目录不存在而失败。"""
    for p in paths:
        if p:
            os.makedirs(p, exist_ok=True)
    return paths


ensure(WORK, LOGS, SHOTS, BACKUP)
