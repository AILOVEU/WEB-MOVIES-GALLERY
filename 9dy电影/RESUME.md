# 久电影 · 老舅收藏 — 离线画廊

> 项目唯一说明文档：既是站点使用说明，也是维护/接手手册。
> 根目录：`D:/workspace/z_WEB-MOVIES-GALLERY/9dy电影`

## 1. 打开与部署

- 本地浏览：双击 `index.html`（「前往原网页观看」需联网）。
- 本地服务：`python -m http.server 8000` → `http://127.0.0.1:8000/index.html`
- **图片两种模式**：头部「图源」按钮切换——`本地` 严格只读 `assets/`（离线可用，默认）；`原图` 直连 `9dy.top` 原图地址（省磁盘，需联网）。两种模式**互不兜底**：本地缺图/加载失败直接显示 🎬 占位，原图模式缺映射或请求失败同样占位，绝不会偷偷改走另一图源。选择记在 `localStorage`。
- **部署只需 3 项**：`index.html` + `assets/` + `data/`（`data.js` + `remote_urls.js`）；下划线目录整体排除。

## 2. 目录结构与命名约定

约定：**下划线开头 = 仅本地维护（不上传服务器）**；不加下划线 = 网站运行必需。

| 目录 | 上传 | 内容 | 误删后果 |
|---|---|---|---|
| `index.html` | ✅ | 单页画廊（框架 + 交互，约 30KB）。**页面框架手工维护、不由脚本生成**（旧单体页生成器已淘汰，无留档） | 站点没了 |
| `assets/images/` | ✅ | 海报 `{id}.jpg` | 卡片显示 🎬 占位；`pipeline/02_new_movies.py --backfill` 重下 |
| `assets/screenshots/` | ✅ | 剧照 `{id}_{n}.jpg` | 弹层剧照丢失；`pipeline/05_download_shots.py` 重下 |
| `data/data.js` | ✅ | `const DATA = [...]`，网站唯一数据源 | 页面空白报错 |
| `data/remote_urls.js` | ✅ | 影片 id → 原站图片 URL 映射（约 216KB），供「图源: 原图」使用。由 `pipeline/08_build_remote_urls.py` 从 `_rawdata/` 离线生成（幂等） | 只能看本地图，按钮自动禁用 |
| `_scripts/` | ❌ | 工具链，按功能分区：`common.py`（公共库）+ `skip_ids.txt`（配置）+ `README.md` + **`pipeline/01_…08_`**（8 个流水线阶段，序号即执行顺序）+ **`tools/`**（login / backup_data / replicate_gallery） | 无法再跑流水线 |
| `_rawdata/` | ❌ | 原站与源数据：`pages/` 快照、`samples/`、`_raw_posts.json` / `details.json` / `magnets.json` | **需重抓**（REST + 详情页 + 磁力，部分依赖账号）；源站改版后不可复原 |
| `_pipeline/` | ❌ | 过程文件，**文件名带日期前缀**：`YYYY-MM-DD_cookies_clean.txt`（每次登录留一份，读取自动取最新）、可选画廊级 `skip_ids.txt` | 需重新 `_scripts/tools/login.py`（当前有效期见第 9 节） |
| `_logs/` | ❌ | 日志（**文件名强制日期前缀** `YYYY-MM-DD_*`）：`magnets-resp/` 响应快照、`shots/` 页面验证截图、`*_crawl_errors.txt`、`*_retry_log.txt` | **无任何数据丢失**，脚本自动重建 |
| `_backup/` | ❌ | 备份按日期分目录：`_backup/YYYY-MM-DD/<原名>.bak` + 同目录 `manifest.txt`（清单/md5）。基准由步骤 0 按需生成，与现行相同的副本不留档（当前为空） | 不丢业务数据，失去回退与 diff 基准 |

`_backup/` `_logs/` `_pipeline/` `_rawdata/` `_scripts/` 各自有 `README.md`（只写本目录独有信息，
通用约定不在它们里重复）。

## 3. 数据现状与校验

收录 **2422** 部（源站 2429 帖中排除了 7 篇教程/说明类非影片帖，见 `_scripts/skip_ids.txt`）｜
海报引用 **2420**（磁盘 2427 个文件；2 部无封面 + 7 张属被排除帖）｜
剧照引用 **2721**（磁盘 3234 个文件，其中 4 张源站 404）｜最新 id **21315**（2026-10-29）。

数字别凭印象改，随时重算：

```bash
grep -c '"p": "assets/images/' data/data.js         # 有海报的记录数 → 2420
grep -o '"assets/screenshots/' data/data.js | wc -l # 剧照引用数 → 2721
ls assets/images | wc -l ; ls assets/screenshots | wc -l   # 磁盘文件数 → 2427 / 3234
python _scripts/pipeline/02_new_movies.py --dry-run             # 已收录/帖数/新片数/排除命中一行看完
```

### data.js 字段

```
基础: i=id t=片名 y=年份 r=地区 g=类型 s=体积 v=视频格式（多为空）
      db=豆瓣评分 a=主演 dr=导演 du=片长 lb=语言 b=字幕 dt=收录日期 u=原站链接
      p=海报路径 assets/images/{id}.jpg（""=无封面）
扩展: wr=编剧 aka=又名 syn=简介 sh=剧照数组 assets/screenshots/...
      mg=磁力数组（""=已扫描确认无磁力；字段缺失=未扫描） mgv=click2=二次点击取得
```

前端弹层按 `lb` 显示「语言」、按 `b` 显示「字幕」（两者取值重叠但语义不同，勿互填）；`v` 目前几乎全空，
展示页未使用它。

⚠️ **改写 data.js 的唯一姿势**：`common.load_datajs()` → 在记录列表上改 → `common.save_datajs()` 全量重写；
禁止按字符串偏移拼接或裸 `sed` 改结构（曾因此截断首条记录）。表头注释由 `common.DATAJS_HEADER` 生成。

## 4. 页面功能

卡片点击弹详情（导演/编剧/主演/又名/片长/语言/豆瓣/简介）｜磁力角标 🧲/无磁 + 一键复制｜
剧照九宫格点击放大｜深链 `index.html#m{id}`（支持前进/后退与站内切换）｜搜索（片名/演员/导演/年份/类型/地区，简繁互通）｜
筛选（地区/年代十年档/类型，可叠加）｜排序（最新/片名/年份/评分）｜按地区分类视图（**同样跟随搜索/筛选**）｜无限滚动 + 懒加载。

弹层与 URL 双向同步：打开弹层写入 `#m{id}`、关闭即清除（均用 `history.replaceState`，不新增历史条目）。
缺图兜底：剧照加载失败自动隐藏并重算「剧照 (N)」，全缺时整栏不展示；弹层海报失败退回 🎬 占位。

## 5. 数据来源与版权

`https://www.9dy.top/author/admin`（作者「老舅」）。本页面为离线收藏索引，仅保留标题与海报缩略图
用于检索浏览；影片详情与资源请前往原站观看。

## 6. 工具链（`_scripts/`）

Python 3 标准库（海报裁剪可选 PIL）；全部支持 `--dir=<画廊目录>`，**默认自动定位到项目根目录**（由
`common.py` 的位置推导，与脚本在哪个分区无关）；建议统一 `python -X utf8` 运行。
分区与执行顺序、全量/增量用法对比见 `_scripts/README.md`。

| 脚本 | 用法与产出 |
|---|---|
| `common.py` | 目录解析 / HTTP / data.js 读写 / 路径常量 / `read_skip_ids()`（取路径用 `datajs_path()`→`data/data.js`、`meta_file()`→`_rawdata/*.json`、`log_file()`；落盘只能走 `atomic_write()`；排除清单读 `_scripts/skip_ids.txt` 并与 `<画廊>/_pipeline/skip_ids.txt` 取并集） |
| `pipeline/01_fetch_meta.py` | `--update [--max-pages=N]` 抓新帖｜`--fix-img` 只给空 `img` 条目补图 URL｜`--retitle` 离线从 `full_title` 重算片名/年份/地区/类型 → `_rawdata/_raw_posts.json` |
| `pipeline/02_new_movies.py` | 新片海报 + 基础记录追加｜`--dry-run`｜`--limit=N`｜`--backfill` 补齐已收录但本地缺失的海报（并回写 `p`）；自动跳过排除清单里的非影片帖 |
| `pipeline/03_crawl_pages.py` | 详情页 HTML（断点续传）｜`--ids=` `--ids-file=` `--limit=` `--workers=` → `_rawdata/pages/`，失败清单 `_logs/<今日>_crawl_errors.txt` |
| `pipeline/04_parse_details.py` | 全量解析页面 → `_rawdata/details.json`（导演/编剧/又名/简介/剧照清单，幂等） |
| `pipeline/05_download_shots.py` | 下载剧照 → `assets/screenshots/`（断点续传，URL 自动 percent-encode，`--ids=`） |
| `tools/login.py` | `python _scripts/tools/login.py <账号> <密码>` AJAX 登录 → `_pipeline/<今日>_cookies_clean.txt`（按日期留档） |
| `pipeline/06_magnets.py` | 磁力扫描 → `_rawdata/magnets.json`｜Cookie 自动取 `_pipeline/` 下最新一份 `*_cookies_clean.txt`｜`--ids=` `--force` `--click2`（0 金币二次点击）`--refetch-no-magnet`（重扫 VIP/空/错）`--test=<id>`｜`--log-dir=` 改快照位置（默认 `_logs/magnets-resp/`，文件名带日期前缀） |
| `pipeline/07_build_datajs.py` | 合并 details + magnets → `data/data.js`（幂等）｜`--fill-basic` 另用元数据**只填空白**基数字段（t/y/r/g/s/v/b/db/a/dr/du/lb）｜`--clean-basic` 清洗存量脏值（只截断到下一个标签/去尾部「更多…」，不覆写，幂等）
| `pipeline/08_build_remote_urls.py` | 从 `_raw_posts.json`（海报 `img`）+ `details.json`（剧照 `shots`）生成 `data/remote_urls.js`（id → 原图 URL，uploads 公共前缀去重；离线、幂等）｜新增影片后重跑一次即可
| `tools/backup_data.py` | **流水线步骤 0**：先建 `_backup/<当天日期>/`，再把 `data.js` 与三个源数据 JSON 备份进去（保留原名 + 同目录写 `manifest.txt` 清单/md5）｜`--day=` `--reason=`；内容相同跳过、变化则刷新当日快照 |
| `tools/replicate_gallery.py` | `--to=<目标>` 复刻成品画廊（只带 `index.html` `RESUME.md` `assets/` `data/data.js` `data/remote_urls.js`）｜`--limit=N` 取最新 N 部裁剪｜`--no-shots`｜`--dry-run` |

## 7. 流水线（全量重建 / 增量更新）

同一套顺序，全量时不带参数逐步跑完，增量时只对新 id 加 `--ids=`：

```bash
S=_scripts; D="D:/workspace/z_WEB-MOVIES-GALLERY/9dy电影"
# 0. 备份上一版：自动先建 _backup/<今天>/ 再存入（跑坏了可回退 / 做 diff 基准）
python -X utf8 $S/tools/backup_data.py --reason=增量更新 --dir=$D
#    等价手工命令：B=_backup/$(date +%F); mkdir -p $B
#      cp data/data.js "$B/data.js.pre-update.bak"
#      for f in _raw_posts.json details.json magnets.json; do cp "_rawdata/$f" "$B/$f.pre-update.bak"; done

python -X utf8 $S/pipeline/01_fetch_meta.py --update --max-pages=2 --dir=$D   # 1. 元数据 → _rawdata/_raw_posts.json（全量时去掉 --update）
python -X utf8 $S/pipeline/02_new_movies.py --dry-run --dir=$D               # 2. 预览新片（不写文件，无新片则后续免跑）
python -X utf8 $S/pipeline/02_new_movies.py     --dir=$D                     #    海报 + 基础记录 → assets/images/、data/data.js
python -X utf8 $S/pipeline/03_crawl_pages.py --ids=<新id> --dir=$D            # 3. 详情页 → _rawdata/pages/
python -X utf8 $S/pipeline/04_parse_details.py  --dir=$D                      # 4. 解析全部页面 → _rawdata/details.json
python -X utf8 $S/pipeline/05_download_shots.py --dir=$D                      # 5. 剧照 → assets/screenshots/
python -X utf8 $S/tools/login.py <账号> <密码> --dir=$D                  # 6. 登录取 Cookie（跑 click2 前必做）
python -X utf8 $S/pipeline/06_magnets.py --ids=<新id> --dir=$D                # 7. 磁力首轮 → _rawdata/magnets.json
python -X utf8 $S/pipeline/06_magnets.py --click2 --dir=$D                    # 8. 仅对确认 0 金币的影片二次点击
python -X utf8 $S/pipeline/07_build_datajs.py   --dir=$D                      # 9. 合并 → data/data.js
python -X utf8 $S/pipeline/08_build_remote_urls.py --dir=$D                   # 10. 原图映射 → data/remote_urls.js
```

**收尾**：同步 `index.html` 5 处静态总数（title / meta / 品牌徽标 / `#cnt` / 页脚）与本文件第 3 节数字。

**验收自检**（每次必做）：① `pipeline/07_build_datajs.py` 重跑 diff 为空（幂等）；② 与本次步骤 0 生成的
`_backup/<日期>/data.js.pre-update.bak`（路径见该目录 `manifest.txt`）
逐字段比对，只允许新增记录有差异；③ 记录数 == `_rawdata/_raw_posts.json` 帖数 − `skip_ids.txt` 命中数（现 2429 − 7 = 2422）且 `dt` 降序；
④ `p`/`sh` 引用文件落地检查（4 张缺失属基线，见第 10 节）。

## 8. 常见维护操作

| 需求 | 操作 |
|---|---|
| 补无图影片的海报 | `pipeline/01_fetch_meta.py --fix-img`（补 URL）→ `pipeline/02_new_movies.py --backfill`（下载） |
| 让地区/类型/年份等空白字段按最新规则重算并落到网站 | `pipeline/01_fetch_meta.py --retitle` → `pipeline/07_build_datajs.py --fill-basic`（只填空白；**已填过的片名不会被覆盖**，改名需人工校对 data.js） |
| 重扫某批磁力 / 升 VIP 后重扫 | `pipeline/06_magnets.py --ids=<id> --force` / `pipeline/06_magnets.py --refetch-no-magnet` |
| 排除/恢复非影片帖（教程、说明等） | 编辑 `_scripts/skip_ids.txt`（工具链级，跟脚本走，沙箱/副本也生效；一行一个 id，支持 `#` 注释）；画廊级追加排除可用 `<画廊>/_pipeline/skip_ids.txt`（两处取并集）。从 data.js 删行后**必须**同步记入清单，否则 `pipeline/02_new_movies.py` 下次会把它们当新片加回 |
| 做对外分发版 | `tools/replicate_gallery.py --to=<目录> [--limit=200] [--no-shots]` |
| 页面渲染与图片是否正常 | `python -m http.server 8000` 后看首页与任一 `#m{id}` 弹层、查 Network 有无 404 |

## 9. 经验与约定

- **目录名与取路径全部集中在 `_scripts/common.py`**：改目录只动 `BACKUP_DIRNAME` / `RAWDATA_DIRNAME` /
  `PIPELINE_DIRNAME` / `LOGS_DIRNAME` 四个常量；新脚本一律经 `meta_file()` / `datajs_path()` / `log_file()` 取路径。
- **磁力：非 0 金币 / 「仅限VIP购买」一律跳过 `--click2`**（不消耗免费次数），记 `state=vip` + data.js `mg=""`；
  `--click2` 只在确认 0 金币时用，连续失败 3 次脚本会自动中止。
- **`--dry-run` 先行**：任何写文件/联网的步骤先跑 dry-run 看体量再执行。
- **删除前先看引用**：动 `assets/` 或 `_rawdata/` 前用 data.js 引用集核对，避免悬空引用与不可复原丢失。
- **登录态有时效**：当前用的是 `_pipeline/2026-10-10_cookies_clean.txt`（取得 2026-10-10，
  `wordpress_logged_in` **约 2026-11-08 到期**）；跑 `--click2` / `--refetch-no-magnet` 前先 `tools/login.py` 刷新
  （会新增一份当日文件，旧的保留作历史）。`pipeline/06_magnets.py` 自动取修改时间最新的一份，不需改名字。
  手工验证接口可用：`curl -b "$(ls -1 _pipeline/*_cookies_clean.txt | tail -1)" https://www.9dy.top/...`
  （9dy.top 已可直连；需代理时加 `-x http://127.0.0.1:7890 --ssl-no-revoke`）。
- **已关闭 TLS 证书校验**（`common.CTX` 为 `CERT_NONE`，原站证书链不完整）：仅适用于本机的抓取工具，
  不要把这套 `common.py` 直接搬到其他站点/线上服务复用。
- **`_logs/` 与 `_pipeline/` 的文件名强制带日期前缀**（`YYYY-MM-DD_*`）：
  `common.log_file()` / `dated_name()` / `pipeline_file()` 统一实现，已幂等（同名再写不会叠前缀）；
  过程文件读取用 `latest_pipeline_file()`（按 mtime 取最新，兼容无前缀旧文件）。
  人工往 `_logs/shots/` 放截图也要自己加当日前缀。原因：这类文件同名天天产生，
  不带日期就会互相覆盖、也无法定位是哪次跑出来的（Cookie 也就看不出何时登录、何时该换）。
- **解析字段必须“截到下一个标签”，且标签写法有变体**：源站同一栏位会出现 `制片国家 / 地区`（带空格）、
  `演员：`/`地区：`/`年份：`（全角、另一套模板）；只按固定字面 `制片国家/地区` 切分会把后续正文
  整段并进上一字段（曾污染 950 个字段 / 893 部）。清洗存量只许“截断 + 去尾”，**不可“以源覆写”** ——
  `details.json` 自身也有 150+ 条把简介/年份并进了字段，覆写会把本来干净的值改坏。
- **磁力扫描出错 ≠ 无磁力**：`magnets.json` 里 `state=error`（超时/断连）的记录在 data.js 中保持 `mg` 缺失（未扫描），
  不会被当成 `mg=""` 确认无磁；重扫用 `pipeline/06_magnets.py --refetch-no-magnet --states=error`。

## 10. 已知基线（非缺陷，勿反复排查）

- 4 张剧照永久缺失（源站 404）：`13727_1.jpg`、`4416_1.jpg`、`4421_1.jpg`、`4426_1.jpg`。
  前端已做 `onerror` 兜底（隐藏 + 重算计数），不会再显示破图；但打开对应弹层仍会有 1 次 404 探测。
- 2 部无封面（`p=""`）：19266《阎魔神女》、12707《艳女十八招》；`pipeline/02_new_movies.py` 对这类报
  `poster FAIL ... no-url` 属正常（源站本身无图），不是抓取失败。
- `assets/screenshots/` 约 517 个文件、`assets/images/` 7 个文件未被 data.js 引用（存量旧图 + 被排除帖子，别盲目清）。
- 弹层剧照/海报已做 `onerror` 兜底（失败即隐藏，剧照全缺时整栏不展示），不会再出现破图。
- **地区口径已统一**（2026-11-03）：地区 chip / 分类视图分组 / 筛选匹配共用 `regionKey()`（香港→中国香港、
  台湾→中国台湾、大陆/中国→中国大陆、去掉双语别名如 `菲律宾 Philippine`→`菲律宾`），三处数字一致（中国香港 992）。
  仍单独成组：`港台` 6 部（粗粒度标签，有意保留）；`未标注` 归入该组。
- **字段标签边界已修**（2026-11-03）：旧解析器在 `制片国家 / 地区`（带空格）等变体上切不开标签，
  导致后续正文被并进字段（共清洗 950 个字段/893 部）；现已修 `04_parse_details.py`（`clip_label` 截到下一个标签）
  并用 `07_build_datajs.py --clean-basic` 清洗存量（只截断/去 `更多…`，不覆写）。
  再查基线：`r/g/a/dr/du/lb` 字段中含 `语言:/上映日期:/片长:/IMDb/更多…` = 0 条。
  少数 `aka` 末尾仍带源站等级词（如「，三级」），属源文本语义，不做正则臆测截断。
- **无地区/类型的记录**：2026-11-03 已处理——源站的 7 篇**非影片帖**（投稿教程、关于字幕的说明、
  LD/CD/BD 简介、关于AI修复、资源详情讲解及使用、关于影片的类型分类、下载指导教程）已从 data.js 删除
  并记入 `_scripts/skip_ids.txt`（总数 2429 → 2422）；它们的 7 张海报 + 10 张剧照作未引用存量保留。
  现在仅剩 2 条真实影片无地区/类型（19359《夏芷写真集》、9284《舒琪的秘密》，源站标题里就没写）。
