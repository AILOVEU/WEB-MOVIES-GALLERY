# _scripts/ — 工具链（按功能分区）

本地维护目录（不上服务器）。通用约定见 `../RESUME.md` 第 2、6、7 节；本文件只讲**目录分区与执行顺序**。

```
_scripts/
├── common.py                公共库：画廊根目录解析、路径常量与 helper、HTTP、data.js 读写、
│                            原子写 atomic_write()、参数 arg_int()、排除清单 read_skip_ids()
├── skip_ids.txt             配置（非影片帖排除清单，跟工具链走，被 common 按名读取）
├── README.md                本文件
├── pipeline/                主流水线：抓数据 → 生成 data.js（全量与增量共用同一批，只差参数）
│   ├── 01_fetch_meta.py         1  REST 元数据      → _rawdata/_raw_posts.json
│   ├── 02_new_movies.py         2  海报 + 基础记录  → assets/images/、data/data.js
│   ├── 03_crawl_pages.py        3  详情页快照       → _rawdata/pages/
│   ├── 04_parse_details.py      4  解析页面         → _rawdata/details.json   ← 离线可重跑
│   ├── 05_download_shots.py     5  剧照             → assets/screenshots/
│   ├── 06_magnets.py            6  磁力扫描         → _rawdata/magnets.json
│   ├── 07_build_datajs.py       7  合并             → data/data.js（幂等）
│   └── 08_build_remote_urls.py  8  原图映射         → data/remote_urls.js（index.html「原图模式」用，幂等）
└── tools/                   独立功能：可单独调用，被上面的阶段依赖或围绕它使用
    ├── login.py                     登录取 Cookie → _pipeline/<日期>_cookies_clean.txt
    ├── backup_data.py               步骤 0：备份到 _backup/<日期>/（含 manifest.txt）
    └── replicate_gallery.py         复刻成品画廊到别处（分发/备份，自动排除全部 `_` 目录）
```

`pipeline/` 的文件名带 `01_`…`08_` 序号 = 建议执行顺序；`tools/` 无顺序关系，故不加序号。

## 全量 vs 增量：为什么不分两个目录

两个场景跑的是**同一批 `pipeline/` 脚本**，差别只在参数。分两个目录必然出现同一脚本两份副本、
改一处忘一处（本项目历史上就吃过这个亏），所以只留一份，用下表区分用法：

| 阶段 | 全量重建 | 增量更新 |
|---|---|---|
| 0 备份 | `python _scripts/tools/backup_data.py --reason=全量重建` | 同左（**每次必做**） |
| 1 元数据 | `python _scripts/pipeline/01_fetch_meta.py` | `... 01_fetch_meta.py --update --max-pages=2` |
| 2 新片 | `... 02_new_movies.py` | 先 `02_new_movies.py --dry-run` 看有没有新片，有才继续 |
| 3 页面 | `... 03_crawl_pages.py` | `... 03_crawl_pages.py --ids=<新id>` |
| 4 解析 | `... 04_parse_details.py` | 同左（全量重解析，幂等） |
| 5 剧照 | `... 05_download_shots.py` | 同左（断点续传，只补新缺） |
| 6 磁力 | `... 06_magnets.py` → `--click2` | `... 06_magnets.py --ids=<新id>`（VIP-only 不跑 click2） |
| 7 合并 | `... 07_build_datajs.py` | 同左；改过解析规则时加 `--fill-basic` |
| 8 原图映射 | `... 08_build_remote_urls.py` | 同左（读 _rawdata，离线可跑，幂等） |
| 收尾 | 同步 `index.html` 5 处总数 + `RESUME.md` 第 3 节数字 | 同左 |

## 维护约定

- **新脚本放哪**：会写 `data/`、`assets/`、`_rawdata/` 里数据的 → `pipeline/`（并按顺序取 `NN_` 前缀）；
  独立辅助性质 → `tools/`（不加序号）；被复用的一般函数一律进 `common.py`，不要造第二个公共模块。
- **路径**：只通过 `common` 的 helper 取（`datajs_path()` / `meta_file()` / `log_file()` /
  `pipeline_file()` / `backup_dir()`），不要写死目录名；目录名常量在 `common.py` 顶部。
- **落盘**：一律 `common.atomic_write()` / `atomic_write_json()`；日志与过程文件名走
  `log_file()` / `pipeline_file()`（强制日期前缀）。
- **导入**：子目录里的脚本统一用
  `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` 找到 `_scripts/common.py`；
  画廊根目录由 `common.gallery_dir()` 推导（= `common.py` 的上两级，即项目根），与调用脚本在哪个分区无关，
  也可用 `--dir=` 或环境变量 `9DY_DIR` 覆盖。
- **运行**：`python -X utf8 _scripts/<分区>/<脚本>.py`（Windows 中文输出）；不传 `--dir` 时自动定位项目根。
