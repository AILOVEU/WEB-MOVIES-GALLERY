# JavDB 收藏清单爬虫 — 项目续跑说明（RESUME）

> 用途：下次重启本项目（新开会话/机器重启）时，读这份文件即可直接继续，不用重新摸索。
> 另：2026-11-02（晚上）把三个非站点目录改名为带下划线前缀的 `_script/`、`_logs/`、`_pipeline/`
> （过程目录一度叫 `working/` → `_working/`，最终定为 **`_pipeline/`**，语义更准：里面全是流水线的中间产物与进度）
> （与 `_backup/` 一致，目的：让 `assets/`、`data/` 这两个“页面要读的”目录在文件列表里排在最前，
> 并一眼区分“工具/垃圾”与“内容”）；`assets/` 与 `data/` **名字不变**（HTML 直接引用它们）。
> 代码里的路径、脚本、文档已全部跟着改完并复测。
> 同日（傍晚）把 `.workbuddy/memory/` 三份会话笔记提炼进**第 11 节**（演进时间线、页面结构与
> 解析姿势、限速参数、验证方法坑、基线数字、`_backup/` 来历对照），原目录已删除。
> 2026-11-02（下午）：**目录整理** —— 资源入 `assets/{covers,videos,水果派}`，
> 数据与名单入 `data/`（movies.js / magnets.js / want.js / watched.js / 各 manifest / 各终态名单），
> 所有操作脚本入 `_script/`（一次性旧脚本进 `_script/legacy/`），根目录只留文件夹与 html；
> `movies.js` 的 `local`/`video`/`talk` 已改写成 `assets/…`（`_script/build/migrate_paths_to_assets.py`）。
> 同日（凌晨）新增第 10 节：**把 `assets/covers/` 整体搬去别处** —— 新工具 `_script/manifest/covers_manifest.py`
> 把「封面下没下过」的判定从「目录里有没有文件」改成「`data/state/covers_manifest.json` 有没有记录」，
> 与 `videos_manifest.json` 同一条规矩；`covers_check.py` / `users_spider.py` / `merge_collection.py` 都已接入，
> `move_check.py` 也支持 covers 的迁移清单与校验）
> 上一次：2026-11-01（新增第 9 节：**清单增量同步** —— `users_spider.py`（看过/想看）+
> `collection_spider.py --refresh-lists/--recheck/--new-only`（收藏的演員/系列）+ `covers_check.py`（封面真伪）；
> 所有脚本的路径已改为跟脚本自身位置走，不再是老机器的 `C:/Users/wh/WorkBuddy/...`）

---

## 1. 这个项目是做什么的

抓取 [JavDB](https://javdb.com) 用户收藏的两个清单，并展开每个条目下的**全部影片**，落库到本地静态站点，提供和现有 top250 / mylist 一致的深色瀑布流浏览体验。

- 清单 1：**收藏的演员** `https://javdb.com/users/collection_actors`（现为 **175** 个）
- 清单 2：**收藏的系列** `https://javdb.com/users/collection_series`（现为 **122** 个）
- 两个清单使用**不同的 js 配置**（`data/site/actors.js` / `data/site/series.js`），影片详情统一落在 `data/site/movies.js`。
- **磁力暂不获取**；影片**封面下载到本地** `assets/covers/`。
- 入口页面：`collection.html`（顶部 tab 切换演员/系列，点条目进瀑布流，复用 mylist 的深色主题；三个页面均为分页，每页 100 个）。

---

## 2. 目录与关键文件

★ 2026-11-02 目录整理后的布局（根目录只留文件夹与 html）：

```
JAVDB/
├── mylist.html  top250.html  collection.html   ← 入口页（直接 file:// 打开）
├── RESUME.md                                      ← 本文件
├── assets/     covers/  videos/  水果派/          ← 资源本体（会被手动搬去别的硬盘）
├── data/     site/（页面读的 7 个 js）+ state/（清单与终态名单） ← 站点数据，跟仓库一起版本化
├── _script/    所有操作脚本（py + 编排 sh + node 助手）└─ legacy/ 首轮建站的一次性脚本（别再跑）
├── _logs/      日志与验证截图（截图入 _logs/shots/）  ← **随时可删，删了不丢任何数据**
├── _pipeline/  断点 ckpt / cookie / hlstmp / probe / samples / jdb_chrome  ← **过程中文件，删了会丢进度**
├── _backup/    历史备份（旧封面、movies.js 各阶段快照、首轮旧页面）
```

★ `.workbuddy/` 已清空并删除（2026-11-02）：里面的三份会话笔记已提炼进本文件**第 11 节**，
日志/断点/脚本此前已移到 `_logs/`、`_pipeline/`、`_script/`。

★ `_pipeline/jdb_chrome/`（旧名 `<repo>/.jdb_chrome`，2026-11-02 已挪）= 专用的 **Chrome 持久 profile**：
`launch_chrome.sh` 用 `--user-data-dir` 指向它，在内过一次 Cloudflare 质询 + 登录 JavDB，
再用 `dump_cookies.js` / `refresh_cookie.js` 走 CDP(9222) 把 `_jdb_session`+`cf_clearance` 抽到
`_pipeline/javdb_cookie.txt`。**删了它 = 登录态与 CF 放行全没，得重新手动过一遍**（约 275 MB）。

判定口诀：**删了会不会丢东西？** 不会（日志/截图）→ `_logs/`；会（断点、cookie、待办名单、
抓取样本）或程序本应自己删的中间产物（HLS 切片）→ `_pipeline/`。

### `assets/`（资源本体，路径都写进了 movies.js）
- `assets/covers/` — 影片封面 `assets/covers/{CODE}.jpg`（会被手动整体搬去别的硬盘/别处建站；见第 10 节）
- `assets/videos/` — 已下载的预告片 `assets/videos/{CODE}.mp4`（会被手动搬到移动硬盘）
- `assets/水果派/` — 影片**解说**视频 `assets/水果派/{CODE}.mp4`（人工放入或由 `_script/tg_video_dl` 下载，番号命名；见第 8 节）
  - ℹ 该目录**只放视频**：下载器 `tg_dl.py` 的已下载记录已于 2026-11-04 改存
    `data/state/tg_manifest.json`（以前叫 `assets/水果派/_tg_manifest.json`，会跟着目录被搬走 → 风险）；
    工具侧 `--manifest` 可覆盖路径，老位置的文件若还在会自动回退并搬家。

### `data/`（数据与名单，拆成两层）
**`data/site/` = 页面要读的（HTML 里 `<script src="data/site/…">` 写死）；`data/state/` = 抓取状态，页面不读。**

#### `data/site/`（7 个 js，被 HTML 引用）
- `movies.js` — `window.JAVDB_MOVIES`（**统一影片库**，三页都读）
  - 里面的 `local` / `video` / `talk` 是**相对站点根的路径**，已随布局改成 `assets/covers/…`、`assets/videos/…`、`assets/水果派/…`（迁移脚本 `_script/build/migrate_paths_to_assets.py`）
- `magnets.js` — 磁力链（三页弹层都读）· `actors.js` / `series.js` — 收藏的演員/系列（collection）
- `top250.js` — 榜单（只存 `{rank,code}`，详情去 movies.js 取）
- `want.js` / `watched.js` — 想看名单 / 看过+我的星级（mylist）
- ⚠ 这 7 个**文件名与目录名都不能改**（HTML 写死引用；11 处 `<script src>`）

#### `data/state/`（7 个 json，页面不读，全部是脚本的判定依据）
- `covers_manifest.json` — **封面已下载清单**（判定「重不重下」的唯一真相，文件搬走记录不删）
- `videos_manifest.json` — **预告片已下载清单**（同上，见第 5.5 节）
- `videos_no_preview.json` — 确认「没有预告片」的番号终态名单（第 7 节）
- `covers_unavailable.json` — CDN 上确实拿不到的封面名单（默认不再重试，只记 403/404/410）
- `media_unmatched.json` — 目录里有、但番号对不上 `movies.js` 的视频文件清单
- `move_manifest.json` — `assets/` 三个目录的**迁移清单与校验基准**（`move_check.py export` 产出）
- `tg_manifest.json` — **TG 解说视频的已下载记录**（`_script/tg_video_dl/tg_dl.py` 读写；key=`{chat_id}_{msg_id}`，
  值只存文件名/体积/msg/日期；2026-11-04 从 `assets/水果派/` 搬进来，这样资源目录整体搬走也不会丢档案；
  由 `_script/manifest/tg_series_gap.py` 用于「哪些系列片子还没下过」的对账）
- ℹ **不再产出 `.md` 副本**（2026-11-02）：`report`/`export` 的可读摘要只打印到 stdout。
  原因：那些 md 代码只写不读、容易看到过期数字，`videos_manifest.md` 更是把 1,800+ 条又列了一遍（277 KB 纯重复）。
  要留档就重定向到可随时删的 `_logs/`：`python _script/manifest/videos_manifest.py report > _logs/videos_view.txt`；
  看明细更推荐现查：`list --present` / `list --gone` / `missing <候选文件>` / `stats`。
- 去别处建站：只要那台机器还会跑 `_script/`，**`data/state/` 必须一起带**（缺了就当“从没下过”全量重抓）；
  纯展示、不在那边跑脚本的话，只带 `data/site/` 就够

### `_script/`（操作脚本，按功能分子目录；全部「跟脚本自身位置走」定位）
2026-11-03：脚本按功能拆入子目录。每个 .py 顶部有一段统一引导 `__JDB_BOOT__`：
从自身文件**向上找到含 `data/` 的目录 = 仓库根 `ROOT`**，再把 `_script` 及其全部子目录加入 `sys.path`
（于是跨子目录也能 `import covers_check` 等）。约定派生：`DATA=ROOT/data`（下分 `SITE=data/site` 页面读的 js、
`STATE=data/state` 清单/名单）、`ASSETS=ROOT/assets`、`WB=ROOT/_pipeline`（沿用变量名 `WB`）、`LOGS=ROOT/_logs`、
`SR=ROOT/_script`。**新增脚本时把这段引导拷到文件顶部即可**（放 docstring 之后、任何 import/路径使用之前）。

功能子目录：
- `common/` — 被 import 的公共库：`win_utf8.py`（import 即固定 stdout 为 UTF-8，防日志重定向时 GBK 砸进程）、`atomic_io.py`（断点抗撕裂写 + `log_path/backup_path` 日期命名助手）。
- `cookie/` — 登录/取 cookie（过 Cloudflare）：`launch_chrome.sh`、`dump_cookies.js`、`autologin.js`、`refresh_cookie.js`、`cdp_fetch.js`（node 助手需 Node 21+ 全局 `WebSocket`；本机走 `C:/Users/wh/.workbuddy/binaries/node/versions/22.22.2-6/node.exe`）。
- `crawl/` — 抓取：`collection_spider.py`（收藏清单）、`users_spider.py`（看过/想看）、`magnet_spider.py`（磁力）、`trailers_download.py`/`trailers_sweep.py`（预告片）、`avatars_localize.py`（演员头像本地化）。
- `manifest/` — 清单判定(库+CLI)：`videos_manifest.py`、`covers_manifest.py`、`covers_check.py`、`media_scan.py`、`move_check.py`。
- `build/` — 生成站点数据：`build_collection_js.py`（重建 actors.js/series.js）、`merge_collection.py`（ckpt→movies.js+补封面）、`migrate_paths_to_assets.py`（一次性路径迁移）。
- `orchestrate/` — 后台编排：`run_backlog.sh`、`run_all_trailers.sh`、`run_pre2022.sh`、`run_sweep.sh`（日志写 `_logs/`，开头 `mkdir -p`，`_logs/` 删空也能续跑）。
- `verify/` — 一次性自检/调试：`check_cover_url.js`、`check_top250.js`、`shot_page.js`、`diag.js`、`test_m3u8.js`、`test_preview.js`。
- `legacy/` — 首轮建站的一次性旧脚本，别再跑。

（下面这些脚本现都在对应子目录，仅名字沿用：）
- `collection_spider.py` — 爬虫主程序（Phase A 抓清单、Phase B 展开每条目影片；断点续传 + 自适应限速）
- `merge_collection.py` — 把 `collection_ckpt.json` 的影片**字段级去重合并**进 movies.js + 下载缺失封面
- `build_collection_js.py` — 从 checkpoint 重建 `data/site/actors.js` / `data/site/series.js`
- `users_spider.py` — **看过/想看清单增量同步**（含 `?star=1..5` 拉自己的星级），写 watched.js / want.js / movies.js / 封面，见第 9 节
- `covers_check.py` — **封面真伪体检与修复**（按文件头判 JPEG/PNG/WEBP，不走代理 + thumbs 回退），见第 9 节
- `covers_manifest.py` — **封面已下载清单**维护工具（scan/check/missing/mark/stats/list/report），搬目录前后的关键，见第 10 节
- `videos_manifest.py` — **预告片已下载清单**维护工具（同名 json/md 都在 data/），详见第 5.5 节
- `media_scan.py` — **本地视频目录 → movies.js 播放字段**的同步器（`assets/水果派/`→`talk`、`assets/videos/`→`video`），见第 8 节
- `move_check.py` — 搬目录去别的硬盘用的**清单导出 + 迁移后校验**（status / export / verify --at），
  支持 `assets/` 下 videos / 水果派 / covers 三个目录，见第 8 节末尾与第 10 节。
  ⚠ **目录必须递归扫**（2026-11-04 修）：covers 分桶后顶层只有 0..63 子目录，早前用 `os.listdir` 只数顶层
  → status 报「0 个文件」、export 出的迁移清单会漏掉全部 5.5 万张封面；现在比对键一律退到文件名，
  新盘上层级不同也能对上；`--at` 给资源根或直接给该目录本身都可以。
  另：清单里 `valid=false` 的坏图不再算「在册未挂」的错（单独作为警告），否则 export 被迫加 --force。
- `magnet_spider.py` — 磁力链抓取（写 `data/site/magnets.js`）
- `trailers_download.py` / `trailers_sweep.py` — 预告片下载器与收尾扫描（第 7 节）
- `atomic_io.py` — 断点抗撕裂写入库；`win_utf8.py` — import 即固定 stdout 为 UTF-8（防止日志重定向时 GBK 砸进程）
- `migrate_paths_to_assets.py` — 本次目录整理的一次性迁移脚本（幂等，可复查路径是否还解析得到）

- `run_backlog.sh` / `run_all_trailers.sh` / `run_pre2022.sh` / `run_sweep.sh` / `launch_chrome.sh` — 后台长跑编排
  （日志都写 `_logs/`，开头会 `mkdir -p _logs _pipeline/hlstmp`，所以 `_logs/` 删空也能接着跑）
- node 助手（CDP 抽 cookie / 截图 / 探页）也在这里：`dump_cookies.js`、`autologin.js`、`cdp_fetch.js`、
  `shot_page.js`、`verify_tabs.js`、`check_*.js`、`fetch_*.js`、`diag*.js`、`probe_*.js`（用 **Node 21+**，
  要全局 `WebSocket`）
- `legacy/` — 首轮建站用的一次性脚本（top250/rankings/parse_build/reorganize/rekey 等），
  很多仍写死老机器路径或用体积判真伪，**别拿来跑增量**

### `_logs/`（日志与验证截图 —— **随时可删，删了不丢任何数据**）
- `_logs/*.log` / `*_log.txt`：backlog_*（欠账总控）、master_run / pre2022_run / sweep_run / trailers_run（预告片）、
  collection_run / spider_* / recheck_run（爬虫）、covers_* / dl_*(封面)、magnets_run / magnet_fill_log（磁力）、
  users_sync_run / merge_log
- **`_logs/shots/`** — 浏览器实测截图统一放这儿（`shot_*.jpg` / `shots_*.jpg` / `verify*.png`，现 28 张 5.2 MB）：
  弹层布局、降级占位、封面渲染等验收证据。以后手动跑 `shot_page.js` 或让助手截图，**输出路径一律写
  `_logs/shots/<名字>.png`**，别再散在 `_logs/` 根下。
- 脚本写日志前会 `os.makedirs(LOGS, exist_ok=True)`，删光 `_logs/` 不会影响任何进度

### `_pipeline/`（过程中文件 —— **删了会丢进度，不要随手清**）
- `collection_ckpt.json`（+ `.b` 交替槽位）— 收藏爬虫**断点**（actors / series / movies），进度真相来源
- `users_ckpt.json`（按日期算本轮）/ `trailers_ckpt.json`（+ `.bak` / `.corrupt-torn` 历史残留）/
  `magnets.json` / `magnets_fill_ckpt.json` — 各任务断点
- `javdb_cookie.txt` — JavDB 登录 Cookie（`_jdb_session` + `cf_clearance`），登录墙判定依据；
  `javdb_login.txt` 登录账号信息，`javdb_cookie.txt.old` 上一份
- `previews.json` / `previews_errors.json` — 预告片探测结果；`rank_covers_need.json` / `top250_need.json` — 待办名单
- `hlstmp/` — HLS 切片临时目录（`slot_0…slot_{workers-1}`，2026-11-02 起槽数=并发数、不再累积；
  旧版按线程 id 命名残留了 161 个目录 / 4.1 GB，**可直接 `rm -rf _pipeline/hlstmp`**，续传不依赖它）
- `probe/` — 探测输出（cov.bin / diag.txt / faketarget/ 等）；`samples/` — 抓回的 96 份页面 HTML 样本
  （离线复跑解析用，重新抓也能得到）
- `jdb_chrome/` — **Chrome 持久 profile**（旧名 `<repo>/.jdb_chrome`，2026-11-02 挪进来）：
  登录态与 `cf_clearance` 都养在这里，约 275 MB；`_script/cookie/launch_chrome.sh` 按脚本自身位置定位到它。
  **挪动/删除前确保 Chrome 没在跑**，否则文件被占用；删了就得重新过 CF 质询 + 登录。
- `_pipeline/<YYYY-MM-DD>/` —（2026-11-03 约定）**每次跑新产生的“过程文件”落当日日期夹**，
  统一入口 `atomic_io.record_path(name)`（已建的：`videos_manifest/covers_manifest 的 missing --out` 就写这里）。
  ⚠ 但**单一当前态的 resume 文件（各 `*_ckpt.json`(+`.a/.b`槽) / `javdb_cookie.txt`）仍固定名留 `_pipeline/` 根、不进日期夹**，
  否则明天换目录会读不到旧进度→从头重抓；“正在跑的不处理”，只对新写入生效。

### （已删除）`.workbuddy/`
- 原本只余 `memory/*.md` 三份会话笔记，已提炼并入本文件**第 11 节**，目录于 2026-11-02 删除。

---

## 3. 当前进度

### 现状快照（2026-11-02 目录整理、2026-11-05 凌晨刷新数字；比下面的历史表可信）
| 项 | 数字 | 说明 |
|---|---|---|
| `data/site/movies.js` | **54,928** 部 | `local` 54,752 / `video` 1,161 / `talk` **139**（字段均带 `assets/` 前缀） |
| `assets/covers/` | **55,130 个文件 / 8.2 GB**（64 桶，顶层无散图） | 2026-11-04：迁回仓库根 `covers/` 的 9,936 张 + 全量补下完成；清单 55,130 条（有效 54,954 / 死链 176），无「搬走」记录 |
| `assets/videos/` | **41** 个（近期下的一部分） | 清单共 **1,838** 条（已移走 1,797）→ 也不会重下 |
| `assets/水果派/` | **57 个成品 / 26.13 GB**（顶层扁平，无分片残留） | 2026-11-05 01:20 TG 群补下完成（收藏系列里 2023–2026 发行的 57 部）；`tg_manifest.json` **137** 条、`talk` 记录 **139** 条（含 82 条文件已搬走的老记录）；57 个文件 ftyp 魔数全部合法 |
| 封面欠账 | 待下 **0** 张 + 已知取不到 **176** 张 | 2026-11-04 11:35 全量补下完成（`--repair --retry-unavailable`）：成功 9,872 / 死链 176 / 瞬时失败 0；movies.js 带 local 54,752 全部命中 |
| 收藏清单 | 演员 **175**（已拓 113）/ 系列 **122**（已拓 1） | ckpt 加载正常（resume 数字非 0 = 路径没写错） |

### 历史表（2026-10-12 合并后，仅存档参考）

| 项目 | 总数 | 已完成 | 剩余 | 说明 |
|---|---|---|---|---|
| 演员 actors | 174 | **112 (64%)** | 62 | 累计 32,439 条 演员→影片 引用 |
| 系列 series | 121 | **0 (0%)** | 121 | 尚未开始 |
| 影片 movies（checkpoint） | — | **28,504** 部去重 | — | 爬虫已收集 |
| 影片 movies（movies.js 落盘） | — | **33,910** 部 | — | 已含约 5,406 部其它来源（top250/mylist），未丢失 |
| 封面 covers | — | **9,936 张已下**（其中 82 张是拦截页，valid=false 待重下）+ 3 个 `.part` 残留 | 24,392 张待下 | 真相：`covers_manifest.json`（2026-11-02 scan；已被 2026-11-04 移回并入覆盖，见上表）|

**爬虫状态**（当时）：已停止（无进程、无后台任务），最后日志 `08:00:13`，非正常结束。
**中断原因**：代理端口 `127.0.0.1:7890` 当时未监听（FlClash 进程在跑但没在 7890 提供代理），curl 返回 `HTTP 000`。Cookie 文件仍有效。

---

## 4. 如何继续执行（Runbook）

### 第 0 步：确认环境
1. **代理**：打开 Clash/FlClash，确保 HTTP 代理在 `127.0.0.1:7890` 真正监听（选好节点、开启系统代理）。
   验证：`curl -s --ssl-no-revoke --max-time 20 -x http://127.0.0.1:7890 https://javdb.com -o /dev/null -w "%{http_code}\n"` 应返回 200 而非 000。
2. **Cookie**：若上一步返回登录页/重定向，说明 cookie 失效，需重抽（见第 3 节坑位）。否则跳过。

### 第 1 步：续跑爬虫（断点续传，自动跳过已完成）
Phase A 已收集完 174+121，重跑会 `skip`；直接从 Phase B 接着抓剩余 **62 演员 + 121 系列**。
推荐用「监管循环」跑，机器休眠/偶发崩溃能自动重启，直到真正 `DONE`：

```powershell
$s='D:/workspace/z_WEB-MOVIES-GALLERY/JAVDB/script'
$py='C:/Users/wh/.workbuddy/binaries/python/versions/3.13.12/python.exe'
while($true){
  & $py "$s/collection_spider.py"
  if($LASTEXITCODE -eq 0){ Write-Host 'SPIDER DONE'; break }
  if($LASTEXITCODE -eq 2){ Write-Host 'COOKIE EXPIRED — 需重抽 cookie 后重试'; break }
  Write-Host "spider 异常退出 rc=$LASTEXITCODE，5s 后重启"; Start-Sleep 5
}
```
> 单跑也行：`& $py "$s/collection_spider.py"`（靠 checkpoint 续传，手动重启即可）。

### 第 2 步：爬虫 DONE 后，收尾（合并 + 重建配置）
```powershell
$py='C:/Users/wh/.workbuddy/binaries/python/versions/3.13.12/python.exe'
$s='D:/workspace/z_WEB-MOVIES-GALLERY/JAVDB/script'
# 合并 checkpoint 影片进 movies.js，并下载缺失封面（不加 SKIP_COVERS）
& $py "$s/merge_collection.py"
# 重建 actors.js / series.js（写入各自 codes）
& $py "$s/build_collection_js.py"
```
- `merge_collection.py` 已改为**字段级去重**：同字段取最新（最近一次抓取覆盖）、缺字段增量补、tags 取并集、已下载封面标记(`local`)保留，可安全反复运行（幂等）。
- 若只想合数据不下载封面：在命令前加环境变量 `SKIP_COVERS=1`（PowerShell：`$env:SKIP_COVERS=1; ...`）。

### 第 3 步：核对
- 打开 `collection.html` 看演员/系列 tab 与瀑布流；
- `assets/covers/` 下应有 ~33,910 张封面（或本地已有部分）。

---

## 5. 关键坑（必读，省得重踩）

1. **沙盒文件守卫**：本机沙盒会把 `os.replace` / 临时文件 `.part` / 批量删除当成危险操作**直接中断进程**。所有写盘一律**直接写目标文件**（不要 `tmp`+`os.replace`）。封面下载也是直接写 `assets/covers/{code}.jpg`，不写临时文件。`merge_collection.py` / `collection_spider.py` 已按此改好。
2. **代理是命门**：爬虫与封面下载都走 `http://127.0.0.1:7890`。代理一断，curl 返回 `HTTP 000`，表现为全部请求空响应 → 爬虫会快速"空跑"完却不落任何数据。跑之前务必确认 7890 在监听。
3. **Cookie 失效 / 换代理节点后重取**：`/users/*`、`/actors/*` 带登录墙；fetch 命中 `sign_in`/`window.location.href='/login'` 即 cookie 过期（脚本 `sys.exit(2)`）。**关键：Cloudflare 的 `cf_clearance` 同时绑定「出口 IP + User-Agent」**，FlClash 换节点(IP 变)或 UA 不一致都会 403「Just a moment」。正确重取步骤：
   - 用 `_script/cookie/launch_chrome.sh`（或手改）启一个 **走同一代理 + 开调试端口 + 用已登录 profile** 的可视化 Chrome：
     `chrome --remote-debugging-port=9222 --user-data-dir="<repo>/working/jdb_chrome" --proxy-server="http://127.0.0.1:7890" --new-window "https://javdb.com/"`
     （**别自动去 /users/sign_in 替用户登录**——用户通常已登录，直接复用其会话。）
   - Chrome 要用 `nohup ... & disown` 保活（跨命令会退出）；等 Cloudflare 自动过（标题变正常）。
   - 用 **Node 21+**（本机走 `C:/Users/wh/.workbuddy/binaries/node/versions/22.22.2-6/node.exe`，因 dump 脚本用全局 `WebSocket`）跑 `_script/cookie/dump_cookies.js 9222`，把 `_jdb_session`+`cf_clearance` 写入 `javdb_cookie.txt`。
   - **务必把 `trailers_download.py` 的 `UA` 改成与该 Chrome 完全一致**（看 `http://127.0.0.1:9222/json/version` 的 User-Agent，例如 `Chrome/156.0.0.0`）。
   - 验证：`curl -x http://127.0.0.1:7890 -A "<该UA>" -H "Cookie: <新cookie>" https://javdb.com/v/<有预告的vid>/preview_url` 返回 **JSON `{"url":...}`** 才算通（返回 `/login` = 没登录；返回 403 = IP/UA 不匹配）。
4. **机器休眠会让 curl 挂死**：曾出现单次 curl 卡 25,137 秒（7 小时），因机器休眠时连接被挂起、超时不计休眠时间。所以爬虫要做**短存盘 + 监管循环重启**，避免一条请求卡死整轮。
5. **fetch() 判定逻辑**（已调好，勿回退）：
   - 完整 HTML（`</html>` 且 >5000 字符）= 成功；
   - **空页/末页是正常结果**（某条目影片正好抓完），不是限流，不要冷却；
   - 截断页（有 `<title>` 无 `</html>`）→ 短退避重试；
   - 纯网络失败/len=0 → 短重试 2 次再升级长冷却；
   - Cloudflare/限流特征页 → 长冷却 60–120s。
6. **done 防护**：`it["done"]=True` 仅在**成功取到影片**时才标（抓到 0 部不标），否则网络故障会污染 checkpoint 让该条目被永久跳过。已落实。
7. **key 名 bug 历史**：系列曾因 `kind+"s"` 拼成 `seriess` 报错，已改为显式 `actor→actors` / `series→series`。
8. **路径已不再写死**（2026-11-01 修）：`collection_spider.py` / `merge_collection.py` / `build_collection_js.py` / `users_spider.py` / `covers_check.py` 统一用
   `WB = dirname(abspath(__file__))`、`ROOT = dirname(WB)` 定位。以前写死的 `C:/Users/wh/WorkBuddy/2026-10-10-07-52-17` 已不存在，
   拿旧路径跑会**读不到 checkpoint** → 变成全量重抓（几十小时），这个坑很阴。新脚本一律别再写绝对路径。
9. **封面 CDN 不能走代理**：`c0.jdbstatic.com` 对代理出口 IP 返 **403 + ~4.5KB 的 HTML 拦截页**；拦截页体积能过
   “> 2000 字节”这种老检查 → `assets/covers/` 里堆过 101 个扩展名叫 .jpg 的 HTML，页面全显示占位图（典型的“假成功”）。
   现：下封面**默认直连**（直连实测 200），并且**按文件头 magic bytes 验真**（`covers_check.is_valid_image`）；
   `users_spider.py` / `merge_collection.py` 已改为复用 `covers_check.fetch_cover`，不要再用体积当校验。
10. **部分老封面是真的没了**：`covers/{x}/{id}.jpg` 直连也 403（只有 303 字节的拒绝页）时，`fetch_cover` 会自动改试
    `thumbs/{x}/{id}.jpg`（实测有的老片只有缩略图还在）；两个都拿不到则记进data/ `covers_unavailable.json`，
    下次默认不重试（`--retry-unavailable` 可强制重试）。
11. **坑 1 的对称风险 —— 直接覆写会被硬杀撕裂**：因为沙盒禁 rename，脚本只能直接覆写目标文件。
    2026-11-01 暂停收藏爬虫时 `collection_ckpt.json` 从 11.2MB 被截成 2.3MB（JSON 解不了）。
    已新增 `_script/common/atomic_io.py`：写 `X.a`/`X.b` 交替槽位 → 写后读回 json 自检（解不了就抛异常、主文件保持上一版）
    → 再 copyfile 到主文件；读时依次试 `X`/`X.a`/`X.b` 取第一个能解析的（`collection_spider.py`、`magnet_spider.py` 已接入）。
    **停长任务的规矩**：先 `cp` 断点到 `_backup/` → `taskkill /PID <总控> /T /F`（必须先杀总控，否则监管循环 10s 后
    又把子进程拉起来）→ 用“两次采样目录文件数不变 + JSON 能解析”确认已停且数据完好。
12. **判断后台爬虫是否还活着：只看断点文件的 mtime 是否在推进**。日志每 50/100 条才写一行、长歇期完全静默，
    “日志没动”不等于进程死了；反过来 `ps -W` 在本机**会假阴性**（显示 0 个 python），
    ctypes 进程枚举又会把“我自己刚启动的那个 python”误认成爬虫（曾据此误判存活）。
    正确姿势：`stat _pipeline/collection_ckpt.json` 看 mtime + `tail` 日志 + 采样目录文件数，三者交叉。
    （10-11 磁力收官就是靠 mtime 发现后台任务在 23:04 被环境回收，剩 41 部用 `flush_now.py` 救回 432 部。）
13. **“图加载成功”不等于“图看得见”**：`.poster img{opacity:0}` 要靠 `onload` 加 `.loaded` 才渐显。
    top250.html 从第一版起就漏了 `onload` → 封面全透明，而 CDP 查 `naturalWidth` 一切正常、
    **只有截图能暴露**（10-11 实踩）。验证显示效果必须截图/看可见性，别只测 naturalWidth。
14. **空列表页是正常结果，别当限流**：条目最后一页返回完整 HTML（~39KB）但影片列表为空。早期 `fetch()` 只认
    `movie-list`/`<div class="item">` 才算成功 → 每个条目白重试 5 次 × 冷却 60–120s ≈ **9 分钟**，174 个条目
    = **26 小时白等**。判定改成「有 `</html>` 且 >5000 字符 = 成功」，只有 Cloudflare/限流特征页才冷却。
15. **沙盒批量删除守卫的具体报错**：`os.replace()`/`os.remove()` 在批量文件操作时报
    `[safe-delete][SAFE_DELETE_BULK_GUARD_ERROR] state lock timeout` 并**直接中断进程**
    （10-12 下 3680 张封面时中招，movies.js 因此没落盘）。对策见坑 1：直接写目标文件 + 先存数据再下载。
16. **工具环境两个坑**：① Git Bash 的 heredoc 写文件会把 `\?` 变成 `/?`（`\s`/`\d` 不受影响）——含 `\?` 的
    正则脚本要用编辑工具写入或写后校验；② Bash 工具默认 120s 超时、`sleep 180` 会被 SIGTERM，长任务必须后台跑。
    偶发 `sandbox-center decisionRecord missing` 写盘报错时，走 PowerShell 执行同一脚本可以过。
17. **演员/系列的「N 部影片」是页面原始数，不等于能点开的番号数**：JavDB 演员页 `section-meta` 的数字含重复/已下架条目
    （2026-11-04 篠田優 卡片 2332 vs 实际 codes 去重 2301；175 个演员里 **106 个**对不上：92 个页面数偏大（累计 +739）、
    14 个偏小（如 若月美衣奈 850 vs 879），净差 256 部）。所以它是**双向偏差**，不能只防「虚高」。
    `collection_ckpt.json` 的 `count` 保留页面数供对账，但 `build_collection_js.py` 输出的 `count` 一律取 `len(codes)`
    （原始数另存 `page_total`）；前端卡片数字/排序/汇总/影片视图统一用 `codes.length`，不要再读 `it.count`。
    另：`crawl_item` 曾用「本页 0 新增就 break」提前收工，遇到整页都是重复番号时会把后面的页整段丢掉；
    现已改为：首次/补漏抓取翻满 `pages`（**系列详情页没有 `section-meta`**，`pages` 靠 Phase A 清单里的 `(N)` 兜底），
    `--recheck` 则用「第 1 页无新增就跳 / 连续 2 页无新增就收」护栏，避免一个条目全量重翻。
18. **系列没头像，卡片图靠 `cover` 字段**（2026-11-04 新增）：`build_collection_js.py` 会给 actors.js / series.js
    每个条目算一个 `cover` = 该条目下**最新影片**（`date` 最大）的封面，优先本地 `local`（文件确实在）再回退远程 `img`；
    前端 `itemHTML` 取 `it.avatar_local || it.avatar || it.cover`（演员有头像所以轮不到 cover，122 个系列全部命中本地封面）。
    页面引用已带 `?v=<YYYYMMDD>` 版本号（2026-11-04 加）：`build_collection_js.bump_refs()` 会在每次 rebuild（含爬虫
    `write_js()` 与 `magnet_buckets.py` 收尾）把三个 HTML 的 `data/site/*.js` 引用 + 前端 `BUILD_V` 常量（懒加载磁力桶用）
    刷成当日日期，所以改完数据不必强刷；同一天重复跑不产生额外修改。HTML 改动前先存一份到 `_backup/<日期>/`。
19. **`actors.js` / `series.js` 只有一个生成入口**（2026-11-04 修）：`_script/build/build_collection_js.py` 的 `rebuild(store)`。
    爬虫 `collection_spider.write_js()` 已改为直接调它。以前两边各拼一份 schema，爬虫收尾的 `write_js()` 只写
    `id/name/avatar/count/codes/done`，**会把 `avatar_local` / `cover` / `page_total` 冲掉**（等于跑一次增量就静默退回
    「演员没头像、系列没封面」）。改字段时只改 `build_items()`；`build_collection_js.py` 现在也走 `atomic_io`，
    且 movies.js 读不到/解析出 0 条时直接报错退出，不会静默写空 cover。
20. **`python -m http.server` 不支持 Range，本地预览解说视频拖不动进度条**（2026-11-05 实测）：它回
    `HTTP/1.0 200 OK` + 全量 `Content-Length`，没有 `Content-Range`/`Accept-Ranges`，连 `curl -r 0-1023` 都会被忽略而拖下整份 292 MB。
    `<video>` 需要 Range 才能 seek，所以用它验播放能力会得到「能开播但拖不动」的错误结论——要验 seek 得换支持 Range 的静态服务器；
    验文件本身是否完整应改用 ftyp 魔数 + manifest 体积比对（`media_scan.py` 的 `talksize` 已能对上）。
21. **硬链接文件当同一份资源的多个名字是可行的**：NTFS 上 `os.link()` 普通权限就能建，`ls -ila` 看到同一 inode、链接数 3，
    `du -sh` 只计一份空间；但 `os.walk`/`listdir` 会当成 3 个文件，所以按目录数文件数时要意识到「文件数 ≠ 资源数」。

---

## 5.5 videos/ 已下载清单（增量同步的判定依据，必读）

清单文件：`data/state/videos_manifest.json`（**唯一真相**，key 为文件名；跟仓库一起版本化）。以前还有个 `report` 生成的 `.md` 可读视图，已取消（代码只写不读、还重复 277 KB），`report` 现在只打印到 stdout。维护脚本是 `_script/manifest/videos_manifest.py`。

> ★ 判定铁律：是否已下载**只认清单里有没有这条记录**，**不认 `assets/videos/` 目录里有没有文件**。即使 `assets/videos/` 为空 / 文件不在，只要清单有记录就算已下载、不重下（文件多半被手动搬到移动硬盘/别处）。这样避免“搬走→目录空→误判未下载→重复下载”。

每条记录字段：`file`（文件名，判定主键）、`code`（去扩展名，可按 code 匹配）、`size`、`mtime`、`first_seen`（首次入清单）、`last_seen`（最后一次在目录里看到）、`present`（当前是否还在 `assets/videos/`）、`origin`（`scan`/`mark`）、`note`。

**维护规则（用户定下的长期规则，勿违背）**：
1. `assets/videos/` 出现新文件 → `scan` 追加记录；
2. 手动把文件搬到移动硬盘 → 目录里没了也**只把 `present` 置 false，记录永久保留**，绝不因“文件不在”而删记录；
3. 任何增量下载/同步**先比对清单**：清单里有记录 = 已下载 = 不重下；
4. 清单有记录但目录没文件 → 默认当作“已被手动移动/清理”，**不要**立即重新下载；
5. 只有人工确认某条是误登记（或下载残缺要重下）时，才用 `drop <名字> --yes` 剔记录。

```powershell
$py='python'; $w='<工程根>/script'   # 在本机：d:/workspace/z_WEB-MOVIES-GALLERY/JAVDB/script
& $py "$w/videos_manifest.py" scan -v        # 搬完文件/下完新片后跑一次，对账（幂等，只追加不删）
& $py "$w/videos_manifest.py" check IPX-118   # 单条判定，rc=0 即已下载（文件名或 code 均可）
& $py "$w/videos_manifest.py" missing cand.txt # 批量增量比对：只输出未下载的 code（进给下载器）
& $py "$w/videos_manifest.py" mark XXX-001.mp4 --note 直接存到移动硬盘   # 手动登记
& $py "$w/videos_manifest.py" stats ; & $py "$w/videos_manifest.py" report
```

**下载器接入约定**：写盘前先 `check <code>`（rc=0 就跳过）；批量拉清单时先过一遍 `missing`；下载成功后立刻 `scan`（或 `mark`）落记录，不要等下一轮。脚本内不删除 `assets/videos/` 任何内容，遵守第 5 节坑 1（直接写目标文件，不用 tmp+replace）。

> ★★★ 三份记录，同一条规矩：【只增不删】★★★
> - `videos_manifest.json` 管「**要不要重新下预告**」：有记录 = 已下，文件搬走了也不算没下。
> - `covers_manifest.json` 管「**要不要重新下封面**」（第 10 节）：有 valid 记录 = 已下，** covers/ 被整体搬走后也不会全量重抓 **。
> - `movies.js` 的 `video` / `talk` / `local` 管「**曾经有过、叫什么路径、多大**」：由 `_script/manifest/media_scan.py` 维护（第 8 节），
>   **文件被搬走时记录照样保留**，绝不因为“盘上没这个文件”而删掉字段。页面碰到不在场的记录会显示
>   “本地文件读不到（多半已搬到移动硬盘）”的占位框，而不是一个坏播放器。
>   确实要人工剔除（正常流程永不用）才 `scan --purge-dead`；误删了用 `restore --from _backup/movies_before_media_fields.js` 只补不覆盖地灌回来。

---

## 6. 命令速查

| 目的 | 命令（PowerShell） |
|---|---|
| 续跑爬虫（监管循环） | 见第 1 步循环 |
| 合并 + 下封面 | `& $py "$s/merge_collection.py"` |
| 只合并不下载 | `$env:SKIP_COVERS=1; & $py "$s/merge_collection.py"` |
| 重建 actors/series.js | `& $py "$s/build_collection_js.py"` |
| 看进度 | 读 `collection_ckpt.json`：`actors`/`series` 各条目 `done` 字段；`movies` 条数 |
| 测代理 | `curl -s --ssl-no-revoke --max-time 20 -x http://127.0.0.1:7890 https://javdb.com -o /dev/null -w "%{http_code}\n"` |
| 已下载清单对账 | `& $py "$s/videos_manifest.py" scan -v` |
| 查某个视频是否已下 | `& $py "$s/videos_manifest.py" check <code或文件名>` |
| 算出还差哪些视频 | `& $py "$s/videos_manifest.py" missing <候选清单.txt>` |
| **同步可播放字段**（新文件入目录后跑；只增不删） | `python _script/manifest/media_scan.py` |
| 看本地视频记录（在场/已移走） | `python _script/manifest/media_scan.py stats` |
| 查某番号有无解说/预告 | `python _script/manifest/media_scan.py check <code> --source talk` |
| 误删记录后从备份灌回（只补不覆盖） | `python _script/manifest/media_scan.py restore --from _backup/movies_before_media_fields.js` |
| **LTX 资源库增量抓取**（只增不删；先看量再落盘） | `python _script/crawl/ltx_fetch.py --dry-run` → `python _script/crawl/ltx_fetch.py`（收尾自动写本轮工作集 `_pipeline/<日期>/ltx_new_codes.txt`，只作留档复核） |
| ~~LTX 新 magnet 补体积~~ **已停用，不要跑**（2026-11-06 定：以后获取 ltx 一律跳过 `ltx_magnet_size.py`，就用没 size 的版本） | 命令仍可用（`--codes-file` / `--codes` 可收窄），但增量流程里没有这一步；magnet 的 `size`/`sizeMB` 留空即为终态 |
| **看过/想看增量**（~110 请求/4–5 分钟） | `python _script/crawl/users_spider.py` |
| 只看清单差异不写盘 | `python _script/crawl/users_spider.py --dry-run` |
| **收藏清单增量**：发现新演員/新系列 | `python _script/crawl/collection_spider.py --refresh-lists --lists-only` |
| 只抓本轮新发现的条目 | `python _script/crawl/collection_spider.py --new-only` |
| 复查已收藏条目有无上新片 | `python _script/crawl/collection_spider.py --recheck` |
| **封面真伪体检 / 修复** | `python _script/manifest/covers_check.py` 、`--repair --bad-only` |
| **封面已下载清单对账**（扫 covers/ 登记） | `python _script/manifest/covers_manifest.py scan` |
| 查某张封面是否已下过 | `python _script/manifest/covers_manifest.py check <CODE>`（rc=0 = 已有，不重下） |
| 算哪些封面还没下 | `python _script/manifest/covers_manifest.py missing <候选清单.txt>` |
| 看“搬走后还剩多少新封面要搬” | `python _script/manifest/covers_manifest.py list --present`（= 当前还在目录里的） |
| 演练“covers/ 已搬走”的判定（不动真数据） | `JDB_COVERS_DIR=/tmp/empty python _script/manifest/covers_check.py --dry-run` |
| **TG 群全量番号扫描**（只抽番号不下载，写缓存 `_pipeline/tg_videos_all.json`） | `_script/tg_video_dl/.venv/Scripts/python.exe _script/tg_video_dl/scan_codes.py` |
| **TG × series × 已下 三方对账**（列从未下载的番号） | `python _script/manifest/tg_series_gap.py`（名单落 `_pipeline/<日期>/series_not_downloaded*`） |
| 按名单下 TG 视频 | `_script/tg_video_dl/.venv/Scripts/python.exe _script/tg_video_dl/tg_dl.py --codes _pipeline/<日期>/series_not_downloaded.codes.txt` |
| **TG 合集帖定点补下**（`--codes` 跑不出来的番号，按 msg id 直下 + 硬链接） | `_script/tg_video_dl/.venv/Scripts/python.exe _script/tg_video_dl/dl_msg.py --ids 1383 --alias SONE-174,SSIS-928,SSIS-889`（先加 `--dry-run` 看这条帖是什么） |
| 按发行年份筛 TG 待下名单 | `python _script/manifest/tg_series_gap.py --since 2023 --until 2026 --out _pipeline/<日期>/series_2023_2026.txt` |
| **LTX 磁力表外部并入**（评论区/网盘整理的表，补站内 api 没有的链接） | `python _script/build/ltx_merge_table.py --csv <一览表.csv> --dry-run` → 去掉 `--dry-run` 落盘（明细 `_pipeline/<日期>/ltx_table_added.json`） |

### 6.1 LTX 磁力库（`data/site/ltx.js`）一步到位

增量只有一步：`ltx_fetch.py`（源 `https://ltx.libray.workers.dev/api/items`，走代理、不要 cookie）。
它把远端 `code -> links` 按「只增不删」并进 `ltx.js`：**新番号整条追加，旧番号只补没见过的链接**，
旧链接与旧番号永远保留；改前快照落 `_backup/<日期>/ltx.js`，原始响应落 `_pipeline/<日期>/ltx_items.json`。
`ed2k` 的体积直接来自链接第 4 段（自带），**`magnet` 的 `size`/`sizeMB` 一律留空就是终态**：
2026-11-06 起按用户规矩**不再执行 `ltx_magnet_size.py`**（那步要按 info_hash 去 sukebei.nyaa.si 逐条查，
站点经常不通、确认查不到的还会被反复重扫）。脚本留在 `_script/crawl/` 不删，将来真要补体积可单独跑，
但**它不属于获取 ltx 的流程**。前端对空 size 本就是 `${m.size ? ... : ''}`（不显示体积栏），排序退化到
`sizeMB||0` 也不影响浏览，所以留空没有任何副作用。2026-11-06 那轮 8 条已在库里的 magnet 体积是历史产物，
按「不改动旧数据」不清掉。三个页面（`mylist` / `top250` / `collection`）直接 `<script src="data/site/ltx.js?v=…">`
读它，不分桶。⚠ `ltx_fetch.py` **不会**自己刷版本号（它只写 `ltx.js`，不像 `collection_spider.write_js()` 那样带收尾），
所以跑完要补一句 `python -c "import sys;sys.path.insert(0,'_script/build');import build_collection_js as b;print(b.bump_refs())"`
把三个 HTML 的 `?v=` 与 `BUILD_V` 刷成当日日期，否则浏览器吃磁盘缓存、页面还是旧数据（2026-11-06 那轮是手动补的）。

### 6.2 LTX 一览表 CSV 并入（`ltx_merge_table.py`）

用户会拿到站外整理的 LTX 表（例：`D:\AllDownload\百度云\LTX\141部LTX破解\141部LTX2.5模型无码破解AV链接一览表.csv`，
从 21 MB xlsx 转的无图版）。这类表里常有 `api/items` 没给的链接（同番号不同版本 / 不同哈希），走
`_script/build/ltx_merge_table.py` 并进 `ltx.js`，**只增不删、按 link 字符串去重**，schema 复用 `ltx_fetch.parse_link`。

番号归属优先级（`resolve()`，写错的代价是把链接挂到别的番号上，所以宁可放弃也不猜）：
1. 「视频链接」列的 javdb id → `movies.js` 的 code（最权威）；
2. 文件名片段的**字面命中**现有 key（试 带/去前导零、带/不带数字前缀、带/不带短横）；
3. 归一命中（`norm` = 字母段 + 去前导零数字）且不撞冲突组；
4. 撞冲突组（库里同时有 `SERO-0211`/`SERO-211`、`390JAC-068`/`JAC-068` 这类，实测 **107 组**）→ **不猜、不新建**，列入「无法归属」；
5. 库里彻底没有 → 才新建 key，并在报告里单列「新建 key」让人过一眼。

空链接行整行丢弃（不留任何记录），表内重复链接只写一次；当日已有 `ltx.js` 备份时改写为 `ltx_before_<HHMMSS>.js`，
避免同日两步互相覆盖改前快照。2026-11-06 首跑：表 168 行 → 已有链接 125、表内重复 5、空链接丢弃 4、
无法归属 1（一条只有 hash 的裸 magnet），实写 **33 条链接 / 32 个番号，新建 key 0**（番号 1715 不变，链接 1769 → 1802）。

---

## 7. 预告片下载（TOP250 / 各榜单）

把榜单里的影片**预告片**（JavDB 官方 preview，HLS/AES-128）下到 `assets/videos/{CODE}.mp4`。已下过的**不重下**——判定依据是第 5.5 节的 `videos_manifest.json`（清单里有记录=已下，即便文件已被搬走也不重下），外加本任务自己的断点 `trailers_ckpt.json`。

### 关键脚本（都在 `_script/`）
- `trailers_download.py` — 下载器主程序。番号→javdb 内部 id(vid) 取自 `movies.js`；走 `/v/{vid}/preview_url` 拿签名 m3u8，再抓 key + ts 切片，ffmpeg 封装。**默认 4 并发**。
- `run_all_trailers.sh` — 总控：等首轮跑完→补跑 `all`→依次 `y2026..y2020`（每个两遍）。
- `run_sweep.sh` — 收尾：等总控结束后用 **2 并发** 把各榜单偶发失败项再兜一遍。

### 数据与判定要点（踩过的坑）
1. **番号≠vid**：`preview_url` 只认 javdb 内部 id（如 `x9DQ6`），不是 `IPX-118`。用 `movies.js` 的 `code→id` 映射。
2. **签发的 url 恒返回，真有没有预告看 m3u8 的 HTTP**：JavDB 对无预告的片也会签出一个 m3u8 地址，但抓它是 **404**。下载器把 m3u8 404 归类为 `nopreview`（**终态**，重跑不再试）；`key_fail/seg_fail/m3u8_fail(非404)/ffmpeg_fail` 是**瞬断**，可重试。
3. **切片/密钥在另一台 CDN**（如 `h1.hnhr01.com`），单请求很快；但**高并发会争抢代理连接**导致 key/seg 请求超时（表现为 key_fail 耗时 ~45s）。已用**整片级重试(3 次)+退避+抖动+更长超时**缓解；若仍大量失败，把 `--workers` 调到 2。
4. **代理是命门**：全程走 `127.0.0.1:7890`；跑前脚本会自检 `javdb.com=200`，不通直接退出（rc=2）。cookie 见 `_pipeline/javdb_cookie.txt`（`_jdb_session`+`cf_clearance` 必须带着，否则 `preview_url` 返回登录跳转）。
5. **沙盒守卫与切片目录**：只写目标文件、不删 `assets/videos/`。临时切片放 `_pipeline/hlstmp/slot_<槽号>/`
   （`slot_0 … slot_{workers-1}`，固定的 `index.m3u8/key.key/seg*.ts` 会被下一部覆盖）。
   ★ 旧版按线程 id 命名（`slot_<get_ident()>`）且从不清理 → **每重启一次就多堆一摊**，
   实测堆到 161 个目录 / **4.1 GB**。2026-11-02 改成抢槽号（并发数就是槽数上限，已死线程的槽直接接管，
   超上限则退回线程 id 命名绝不撞车；已用 6×16 任务与超并发两个场景实测验过无冲突），
   从此不需删除也不会累积。现存的旧垃圾随时可清：`rm -rf _pipeline/hlstmp`（续传不依赖它，
   状态在 `_pipeline/trailers_ckpt.json` + `data/state/videos_manifest.json`）。
6. **★无预告持久记录★（与“已下/迁移”分开）**：m3u8 返回 404 = 真无预告且以后也不会有，写入独立的data/ `videos_no_preview.json`（**不进** `videos_manifest.json`，以免与“文件被搬走”混淆）。以后重跑遇到这些番号会**直接跳过、不再解析那个网页**；除非传 `--refetch-no-preview` 强制重取。若某番号后来真下到了预告，会自动从 `videos_no_preview.json` 移除。回填旧判定：用 `trailers_download` 模块的 `load_ckpt()+record_npv()+save_npv()` 从 ckpt 的 nopreview 项补录。
7. **签名有时效 + ffmpeg 绝不能走代理**：`/v/{vid}/preview_url` 返回的 m3u8 签名**约 1 小时过期**（长跑要临取临用）；
   封装时 **ffmpeg 自己去拉远端会 502**，正确管线是纯 curl 拉 m3u8/key/ts 到本地、ffmpeg 只处理本地文件，
   并且必须带 `-protocol_whitelist file,crypto,data -allowed_extensions ALL`（否则 ffmpeg 拒读 crypto/data）。
8. **早期基线**：首次全量探测 1454 部 → 1053 有预告（看过 413 / 想看 640）；看过那 413 三轮下载最终 **380 部落盘（7.0 GB）**，
   其余 33 部稳定 `m3u8_fail` = 预告源站失效，下不到。封面永久 403 的两部：**PB-003、SDAB-049**（走占位图，别再试）。

### 用法
```bash
# 单跑某榜单（断点续传，自动跳过已下与无预告）
python _script/crawl/trailers_download.py --variant all            # TOP250 总榜
python _script/crawl/trailers_download.py --variant y2026 --workers 4
python _script/crawl/trailers_download.py --variant all --dry-run  # 只看要下哪些
python _script/crawl/trailers_download.py --variant all --refetch-no-preview  # 强制重取“无预告”番号
# 全自动（总榜→2026..2020→低并发收尾）
bash _script/orchestrate/run_all_trailers.sh &
bash _script/orchestrate/run_sweep.sh &
# 2022 之前各年（y2021..y2008，workers=4，当前在跑这个）
bash _script/orchestrate/run_pre2022.sh &
```
### 看进度 / 日志
- `_logs/trailers_run.log`（首轮）、`_logs/master_run.log`（总控+各年份榜）、`_logs/sweep_run.log`（收尾）、`_logs/pre2022_run.log`（2022 之前各年）。
- 断点：`python -c "import json,collections;d=json.load(open('_pipeline/trailers_ckpt.json',encoding='utf-8'));print(collections.Counter(v['status'].split(':')[0] for v in d.values()))"`
- 醒后核对：`python _script/manifest/videos_manifest.py scan -v` 已把新落盘文件登记进清单；`ls videos | wc -l`、`du -sh videos`。
- **★下完预告还要多一步★**：`python _script/manifest/media_scan.py --source video`（或直接 `scan`）——
  `videos_manifest` 只记“下没下”，**页面播不播得了看 `movies.js` 的 `video` 字段**；不跑这一步，新下的 740 个预告在页面上是看不到的（第 8 节）。
- **重跑永远安全**：已下（`videos_manifest.json`）/无预告（`videos_no_preview.json`）是终态会跳过；其余失败项会被再次尝试。

---

## 8. 本地视频播放：`assets/水果派/` 解说 + `assets/videos/` 预告片

`assets/水果派/` 是影片**解说**视频的存放目录（人工放入，沿用本项目按番号命名的规则：`assets/水果派/{CODE}.mp4`）。
它与 `assets/videos/`（官方预告片）共用一套接入方式：**目录里的文件 → `movies.js` 的路径字段 → 页面弹层直接播**。

### 字段规范（前端播不播得了，只看这两个字段）
| 源 | 目录 | 路径字段 | 体积字段 | 页面标签 |
|---|---|---|---|---|
| 解说 | `assets/水果派/` | `talk` = `水果派/MFYD-063.mp4` | `talksize`（字节） | 解说 |
| 预告片 | `assets/videos/` | `video` = `videos/ABP-968.mp4` | `videosize`（字节） | 预告片 |

没文件的条目**不带**该字段（新挂载时）；但**一旦写进去就永不删除** —— 文件被搬到移动硬盘/别的磁盘
后，记录与原路径全部保留（`scan` 只会统计“已移走”，不会删），前端对这种记录显示降级占位框而不是坏播放器。
确需人工剔除：`scan --purge-dead`（危险，正常流程不用）；误删后：`restore --from _backup/movies_before_media_fields.js`（只补不覆盖）。

### 同步器 `_script/manifest/media_scan.py`（幂等，可反复跑，**只增不删**）
- `scan`（不带子命令即此）：扫两个目录，**新文件→写字段/刷新路径与体积**，
  **文件没了→记录保留**（并从 `videos_manifest.json` 回填体积），
  **番号对不上→写 data/ `media_unmatched.json`**（不碰数据）。
- `stats` / `check <code> --source talk|video` / `list --source talk`。
- 只读视频目录、只写 `movies.js` + `media_unmatched.json`；**绝不删除/移动目录里的文件，也绝不删已写入的记录**；
  直接写目标文件（第 5 节坑 1）；首次修改 `movies.js` 前备份到 `_backup/movies_before_media_fields.js`。
- 番号解析容错：整名精确 → 正则候选（`MFYD-063` / `010516_222` / `121914-760` / `FC2-PPV-xxx` 等）→ 已知番号子串取最长；
  所以 `水果派/MFYD-063 完整解说 1080P.mp4` 这种带说明的文件名也能对上。同番号多份文件时保留体积最大的一份。
- **日常维护就一句话**：往 `assets/水果派/` 丢了文件 → `python _script/manifest/media_scan.py` → 刷新页面。

### TG 群下载：两类必须绕路的情况（2026-11-05 实测）
1. **合集帖用 `--codes` 永远跑不出来** —— `tg_dl.match_watched()` 一条消息只返回**一个**番号，而 `download_one()`
   的跳过判据是「那个番号是否已下」。msg 1383 正文列了 6 个番号（含我们要的 `SONE-174`/`SSIS-928`/`SSIS-889`），
   但它先命中 `SONE-221`（另一条帖 msg 1755 已单独下过）→ 整条打「跳过已下载 SONE-221」，另外 3 个一起被吞。
   解法 = `_script/tg_video_dl/dl_msg.py --ids <msg> --alias <番号,…>`：按 msg id 定点下，主文件用一个番号命名，
   其余番号建 **NTFS 硬链接**（`os.link`，实测三个名字同一 inode、只占一份 279 MB），这样 `media_scan.py` 能把每个番号都挂上 `talk`。
   查某番号是否属于这类：拿 `_pipeline/tg_videos_all.json`（`scan_codes.py` 的缓存，每条带 `msg` / `msg_codes`）反查它所在消息的番号数。
2. **`--codes` 名单一定要带发行日期筛**，别按 TG 上架日期看 —— `tg_series_gap.py` 的 `--since/--until` 用的是
   `movies.js` 的 `date`（影片发行日），缓存里的 `date` 是群里发帖日，两者能差一年多。
另外：老消息（msg 300~700）常报 `FileReferenceExpiredError` / `Timeout while fetching data (GetFileRequest)`，
脚本每次失败都会 `get_messages` 刷新 file_reference 重试，且分片是**字节级续传**的，所以慢但不会白下（实测 0 个最终失败）。

### 前端（三个页面一致）
`mylist.html` / `top250.html` / `collection.html` 的详情弹层都是**两行布局**（弹层 max-width 1100px），`#mbody` 的子元素固定顺序：`.mbody` → `.mmedia` → `.mmag` → `.mfoot`：
- **第一行：封面大图 + 介绍（永远在最上面）**：`.mbody` 里 `.mside`/`.mposter`（340px / top250 为 260px）与 `.minfo` 并排；
  标题/评分/日期/标签在 `.minfo` 里，所以 CSS 里 `.mtitle`/`.mmeta` 是**全局选择器**，别再写成 `.mside .mtitle`；
- **第二行：视频独占整行**（`.mmedia` 直接放在 `.mbody` 外面，不是与封面挤在一块的 `flex:1`），
  `video` 保持 16:9（`aspect-ratio:16/9` + `max-width:calc(72vh*16/9)` + `margin:0 auto`）尽可能放大；
  无解说也无预告时 `.mmedia` 整块不渲染（实测中间不会出现空洞）；
- **留白约定**：`.mbody{padding:22px}` 自带 22px 下内边距，所以 `.mmedia` 的上内边距必须为 0
  （`padding:0 22px 22px`，窄屏 `0 16px 16px`），否则会叠出 44px；实测视觉间距 22px（窄屏 16px）；
- **默认先播解说**，有解说+预告时上方出现「解说 / 预告片」两个 `.mtab` 切换（切 tab 才换 `src`，不会同时加载两个文件）；
- 播放器下方显示 `文件名 · 体积` 和一个「新窗口打开」链接；文件不在场（已搬到别的磁盘）时 `mediaFail()` 把
  `<video>` 隐掉、换成**紧凑的整宽提示条**（`.merr`，`display:flex`、实测高 112~122px），不留坏播放器也不塌陷布局。
  注意：`.merr` 保持紧凑整宽提示条即可（`.mmedia` 已独占一行，再做 16:9 占位框会变成一大块空黑）。
- 卡片上有蓝色「🎙 解说」/ 橙色「▶ 预告」角标；`mylist.html` 筛选栏新增「本地」行（解说 / 预告 两个 chip，只列当前片单里确实有的）。
- 路径里带中文：`movies.js` 里存**原始路径**（人能看懂），前端用 `encodeURI()` 发射，浏览器会请求 `%E6%B0%B4%E6%9E%9C%E6%B4%BE/...`，已实测 `file://` 下可正常解析。

### 把 `assets/videos/` 或 `assets/水果派/` 搬去别的硬盘（正确姿势）

前提认识：**记录与“文件在不在这块盘”本来就解耦**，搬走不会丢任何东西——
`videos_manifest.json` 有记录 = 已下载（不重下）；`movies.js` 的 `video`/`talk` 记录也**只增不删**。
真正会丢记录的两个动作：手动删 `videos_manifest.json`，以及在搬走后跑 `media_scan.py scan --purge-dead`（**搬目录前后切勿加这个参数**）。

```bash
# 0) 先暂停往里写东西的下载器（tg_dl 会往 水果派/、videos/ 写，搬的过程中写就废了）
# 1) 对账 + 导清单（会逐个文件确认“在册”，有漏会拒绝出清单并提示先跑 scan）
python _script/manifest/videos_manifest.py scan     # 把目录里新落盘的文件登记进清单
python _script/manifest/media_scan.py               # 把目录里的文件挂上播放字段
python _script/manifest/move_check.py export        # -> data/state/move_manifest.json（人看的摘要直接打印，不落盘 .md）
# 2) 拷贝（用会自己校验的工具；拷完不要急着删原目录）
robocopy "D:\\workspace\\z_WEB-MOVIES-GALLERY\\JAVDB\\assets\\水果派" "E:\\JAVDB_media\\水果派" /E /COPY:DAT /DCOPY:DAT /R:3 /W:3 /MT:8 /LOG+:E:\\rc_水果派.log
# 3) 在新盘上校验：按文件名 + 字节数比对，列出缺失/体积不符/额外项
python _script/manifest/move_check.py verify --at E:\\JAVDB_media        # 或 --at E:\\JAVDB_media\\水果派 --dir 水果派
# 4) 只有打印“校验通过”才删原目录
```

两种搬法，看你想不想继续能播：
- **A. 纯归档（页面暂不播）**：拷完删原目录即可。页面会变成“本地文件读不到（多半已搬到移动硬盘）”的提示条，
  角标与记录都在；文件哪天拷回来，重跑 `python _script/manifest/media_scan.py` 就恢复可播。
- **B. 换盘但照旧能播（推荐）**：原位置留个目录 junction 指到新盘，数据与路径完全不用改（无需管理员权限）：
  ```bat
  mklink /J "D:\workspace\z_WEB-MOVIES-GALLERY\JAVDB\assets\水果派" "E:\JAVDB_media\水果派"
  mklink /J "D:\workspace\z_WEB-MOVIES-GALLERY\JAVDB\assets\videos" "E:\JAVDB_media\videos"
  ```
  缺点：新盘没插时目录会变空/报错（此时跑 scan 会全部记成“已移走”，**不会丢记录**，插回来再 scan 就恢复）。

注意：`tg_dl` 的已下载记录现在在 `data/state/tg_manifest.json`，**不再跟着 `assets/水果派/` 一起搬**（2026-11-04 改）；
该目录里只剩视频文件，`move_check.py` 也不会再列到那个 json。工具侧 `--manifest` 可临时指定其它位置。

### 要记住的坑

1. **大文件靠 Range 才能播**：解说片普遍几百 MB，且 ffmpeg 封装时**没加 `-movflags +faststart`**（`moov` 在文件尾部）。
   直接 `file://` 打开没问题（Chrome 自己对本地文件做 range 读尾部）；但用 `python -m http.server` 这类**不支持 Range** 的服务起站，
   大文件会“转圈/播不出来”（得先下完 500MB）。要起服务就用支持 Range 的。
2. **`preload="metadata"`，不自动播放**：跟预告片一致，点卡片→弹层→用户按播放键（浏览器也禁止带声音自动播放）。
3. **与 `merge_collection.py` 共存**：字段级合并保留未知字段，`talk`/`video` 不会被冲掉；但爬虫跑完、或把文件搬到移动硬盘后，
   **重跑一次 `media_scan.py`** 才能把死链清掉 / 把搬回来的文件重新挂上。
4. **顺带修了两个既存 bug**（不修的话这功能在部分页面根本没机会露脸）：
   - `collection.html` 的 `openItem()` 对 `const BYCODE` 重新赋值 → `TypeError: Assignment to constant variable.`，
     点任何演員/系列都进不去瀑布流（已改为只清空键值，不重新赋值）；同时修复“返回条目列表后番号搜索词残留导致列表空”的 UX 问题。
   - `assets/videos/` 里 740 个预告文件全都没挂 `video` 字段，而 380 个旧字段指向的文件已被搬走——页面根本播不了预告片。
     首轮对账时按“清死链”处理，把那 380 条已搬走的记录**误删了**；按【记录只增不删】改正，
     并用 `restore --from _backup/movies_before_media_fields.js` 全部灌回（体积从 `videos_manifest.json` 回填）。
     现在 `video` = **1120 条记录**（在场 740 / 已移走仍保留 380），`talk` = 1 条。
5. **已浏览器实测通过**（2026-11-01）：mylist/collection 弹层播 `水果派/MFYD-063.mp4`（499MB）readyState=4 可正常播放；
   top250 弹层播 `videos/ABP-968.mp4` 、mylist「想看」tab 播 `videos/ADN-334.mp4` 均 `video.error=null`；控制台零报错。
   「记录在但文件不在」的降级也实测过（FNS-253 / SSNI-497）：无 JS 异常，只有一条 `ERR_FILE_NOT_FOUND` 资源级报错。
6. **已移走的记录仍会带「▶ 预告」角标**（前端只看字段有无，运行时无法 stat 磁盘），点进去才知道不在盘上。
   如果以后想让列表侧也不误导，得额外维护一份「当前在场清单」给前端用（待定，未做）。

---

## 9. 清单增量同步（看过 / 想看 / 收藏的演員 / 收藏的系列）

四个入口（全部要带 cookie **且走代理** `127.0.0.1:7890`；页头导航里就带总数，用来算页数）：
`/users/watched_videos`、`/users/want_watch_videos`、`/users/collection_actors`、`/users/collection_series`。

### 页面结构实测（修解析器先看这里，详细版本见第 11.2 节）
- 收藏清单页：演員 = `div.box.actor-box` + `id="actor-{slug}"` + `img.avatar`；
  系列 = `div.box` + `id="series-{slug}"` + `<strong>名</strong><span>(N)</span>`。清单本身只 4 页。
- 算页数：演員页有 `<span class="section-meta">661 部影片</span>`；**系列页没有**，只能拿清单里的 `(N)`。
- 条目影片页是标准 `movie-list`（40/页），**分页控件只显示前几页会被截断** → 必须自己 `?page=N` 探到空页。
- 用户列表条目：番号取 `data-batch-id="xxx"` 属性（`href`/`class="box"` 属性顺序不固定，别依赖它们）。

### 一次完整增量就跑这几步
```bash
# 1) 收藏清单：发现新演員/新系列（~10 请求，十几秒）
python _script/crawl/collection_spider.py --refresh-lists --lists-only
# 2) 只拓本轮新发现条目（不碰历史欠账）
python _script/crawl/collection_spider.py --new-only
# 3)（可选）复查已收藏条目有没有上新片：每个条目 1 个请求，~300 个
python _script/crawl/collection_spider.py --recheck
# 4) 看过/想看（base + star1..5 + want ≈ 110 请求 ≈ 4–5 分钟）
python _script/crawl/users_spider.py
# 5) 合回 movies.js / 重建清单配置 / 修封面 / 同步播放字段
SKIP_COVERS=1 python _script/build/merge_collection.py
python _script/build/build_collection_js.py
python _script/manifest/covers_check.py --repair --bad-only
python _script/manifest/covers_manifest.py scan     # 新下的封面登记进已下载清单（第 10 节）
python _script/manifest/media_scan.py
```

### `users_spider.py` 要点
- 页数按**页头总数**算（`想看 (880)` / `看過 (583)`），不靠“碰到空页”猜；**也不会因为“整页无新增”提前停**
  （清单按时间倒序，新片插在中间，提前停会漏掉后面的存量）。
- **看过列表页上的「x分, 由x人評價」是大众评分，不是我的评分**；我的星级只能靠 `?star=1..5` 分别抓
  （旧脚本 `update_star5.py` 只做了 star=5，所以当年 `watched.js` 里其他值其实是大众评分）。
  star=1..5 都没命中的就是**未打星** → 写 0（不沿用旧值）；前端 `mylist.html` 对 0 会退回显示大众评分。
- **只增不删**：从清单消失的番号默认保留（真删才加 `--prune`）；合并 `movies.js` 是字段级的，
  `local`/`video`/`talk` 一律保留。
- 选项：`--dry-run`（只看差异）/ `--no-stars`（跳过星级）/ `--skip-covers` / `--only watched|want` / `--reset`；
  断点 `users_ckpt.json` **按日期算本轮**，跳天自动重抓（同一天内重跑 = 0 请求，只重算落盘）。

### `collection_spider.py` 的三种选择
- 默认：只跑 `done=False` 的条目（**注意：这会把历史欠账一起拖上来**）
- `--refresh-lists`：重翻收藏清单页，**只新增没见过的条目**，不动已有条目的 `done`；新条目标 `new: true`
- `--new-only`：只拓本轮 `new` 的条目（拓完清标记）——**做增量时用这个，不要用默认**
- `--recheck`：复查 `done=True` 的条目，只翻第 1 页做差异比对，无新片就过（每个条目 1 个请求）
- 写盘已改为直接写目标文件（不再 `tmp + os.replace`，见第 5 节坑 1）

### 本轮（2026-11-01）实际结果
- 看过：583（无新增无移出）；星级全部重算：1 星 22 / 2 星 114 / 3 星 185 / 4 星 186 / 5 星 59 / 未打星 17
- 想看：871 → 站点 880，**新增 11**（CAWB-035 / DASD-495 / DASS-643 …）；站点移出 2 个，按只增不删保留 → want.js **882**
- 收藏的演員 174 → **175**（新：**小松空** 9 部）；收藏的系列 121 → **122**（新：派遣マッサージ師…系列 58 部）
- `--recheck` 复查 114 个已收藏条目：**47 个上了新片**（柏木小夏 +12、姬咲華/羽月乃蒼 +9、彩月七緒/美園和花/月野香澄/有村希 +8 …），
  合计 138 条新引用，共 159 个请求（十几分钟跑完）
- `movies.js` 33910 → **34081**（+171 部）；`actors.js` 175 项（已拓 113）/ `series.js` 122 项（已拓 1）已重建
- 封面：先查出 **101 个“假 .jpg”**（CF 拦截页），直连重下救回 158 张（56 旧 + 102 新），
  最终 `movies.js` 挂的 9269 个 local **全部是真图片**；拿不到的 51 个记入 `covers_unavailable.json`
- `assets/水果派/` 已从 1 个涨到 **82 个解说片**（37.56 GB，用户的 tg_dl 在持续下），media_scan 一次挂上 81 条、未匹配 0；
  其中 78 部同时有预告片 → 弹层会出现「解说/预告片」双 tab；`video`/`talk` 记录在合并后完好无损（1120 / 82）

### 已知欠账（不是增量，要干就单独拉任务）
- 收藏条目未拓完：**62 个演員 + 121 个系列**（系列从一开始一行没跑）→ 跑不带 `--new-only` 的 `collection_spider.py` 会接上，预计十几小时
- 封面缺 **~2.47 万张**（`covers_check.py` 体检数字；`--repair` 不带 `--bad-only` 就是全量下）
- 新片的**磁力链接**未抓（`magnets.js` 是旧数据）；要补得先适配 `magnet_spider.py`（仍是旧路径）
- 9 条影片的 `title` 自带番号前缀（页面上 tooltip 番号重复），属小瑕

### 一键清欠账：`bash _script/orchestrate/run_backlog.sh`
上面三项欠账 + 小瑕清洗已编排好（2026-11-01 实跑），阶段串行、同阶段内并行，且**错开写同一个文件的任务**：
1. 收藏补拓（`collection_spider.py` 默认模式，只跑 done=False）‖ 封面全量补齐（直连 CDN）
2. `merge_collection`（`SKIP_COVERS=1`）→ `build_collection_js` → `media_scan`
3. 磁力分批（先 `--scope watched` 再 `want` 再 `missing`）‖ 新片封面
4. 收尾对账（media_scan + videos_manifest + covers 体检 + `covers_manifest.py scan`）
```bash
nohup bash _script/orchestrate/run_backlog.sh > _logs/backlog_nohup.log 2>&1 &   # 后台跑
tail -3 _logs/backlog_master.log _logs/backlog_covers.log _logs/backlog_spider.log
curl -s -o NUL -w "%{http_code}\n" "https://c0.jdbstatic.com/covers/xx/YY.jpg"   # 先确认直连还能拿图
```
可调环境变量：`CVW`（封面并发，默认 8）、`MGW`（磁力并发，默认 2，走代理别贪高）、
`MAG_ALL=0`（磁力只补看过/想看，不扫全库）、`MAG_CAP`（每部最多保留多少条磁力，默认 40，控制 magnets.js 体积）。
全部可断点续传（ckpt / 只增不减），cookie 失效时子进程 rc=2 会停住并写清原因，重抽 cookie 后重跑本脚本即可接着跑。

### 验收记录（2026-11-01，浏览器实测）
mylist：看过 583 / 想看 882 计数正确；DASD-495、CAWB-035 等新片卡片能渲染；未打星（WREC=0）能退回大众评分
（实例 JUR-558 → ★ 4.3）；零 JS 异常。collection：175 位演員、小松空 9 部能进入并渲染、新系列 58 部能渲染。

---

## 10. 把 `assets/covers/` 整体搬去别处（封面不重下）

场景：封面已经积累到 ~1 万张（1.4 GB），用户会**手动把 `assets/covers/` 整体搬到别的文件夹/硬盘**，
在别处建站时用全量封面；本站这边以后**只新增**。旧逻辑的隐患：“这张封面下没下过”
只看 `assets/covers/` 里有没有文件 → 目录一空，**全部老封面被当成未下载重抽一遍**（~1 万次请求）。
本次改动已经把判定改成“看清单”，并当场把现有 9,936 张全量登记好。

### 判定铁律（与第 5.5 节 videos 一致）
**是否已下载只认 `covers_manifest.json` 里有没有 valid 记录，不认 `assets/covers/` 目录里有没有文件。**
即使目录空了 / 文件不在，只要清单有 valid 记录就算已下载、不重下；记录永久保留。

| 情况 | 判定 | 行为 |
|---|---|---|
| 目录里有真图片（文件头验证过） | 已有 | 不重下，**顺手登记进清单** |
| 目录里没文件，清单有 valid 记录 | 已搬走 | **不重下**；`movies.js` 的 `local` 照样挂/保留 |
| 目录里的文件不是图片（拦截页/残缺） | 未下 | 清单标 `valid=false` → `--repair` 重下 |
| 清单 `valid=false` 且文件也没了 | 未下 | 归入“缺失待下”，会重下 |

登记时机（三处都接了）：`covers_check.py --repair` 下成功当场登记（每 200 张同步落盘）；
`users_spider.py` 新片封面当场登记；`merge_collection.py` 下载/从 `_backup` 拷回都登记。
因此**不管走哪条路新下的封面，都不会出现“下完了却没登记”→下次重下**。

### 搬之前（必做，一次性）
```bash
python _script/manifest/covers_manifest.py scan          # 把当前 covers/ 全量登记（2026-11-02 已跑）
python _script/manifest/covers_check.py --repair --bad-only   # 先修掉“假 .jpg”（别把垃圾拷进归档）
python _script/manifest/covers_manifest.py stats         # 核对记录数 == 目录图片数
python _script/manifest/move_check.py export             # 出迁移清单（含 covers，对账不过会拒绝出清单）
```
### 搬到新位置 + 校验
```bash
robocopy "D:\\workspace\\z_WEB-MOVIES-GALLERY\\JAVDB\\assets\\covers" "E:\\JAVDB_media\\covers" /E /COPY:DAT /R:3 /W:3 /MT:16 /LOG+:E:\\rc_covers.log
python _script/manifest/move_check.py verify --at E:\\JAVDB_media --dir covers   # 文件名+字节数逐个比
python _script/manifest/covers_manifest.py report        # 把可读视图打印到 stdout（不再落盘 .md）
# 只有打印“校验通过”才删原目录
```
### 搬之后怎么确认“不会重下”（不动真实数据就能验）
★ 2026-11-02 下午目录整理后，封面位置是 `assets/covers/`；**用户已经把 9,936 张封面搬走了**，
所以 `covers_manifest.py stats` 现在是「记录 9936 / 目录内仍在 0 / 已搬走 9936」——
`covers_check.py` 体检报 `已搬走(凭清单不重下) 9611 | 缺失待下 24392`，正是设计行为。
```bash
# 用空目录假装“covers/ 已搬走”，看体检数字
JDB_COVERS_DIR="$PWD/.empty_covers_dryrun" python _script/manifest/covers_check.py --dry-run
```
2026-11-02 实测（真目录还在时用环境变量指向空目录）：
- 正常判定：`已搬走(凭清单不重下) 9611 | 损坏 0 | 缺失待下 24392` —— **待下数与目录存在时一致**（就是真欠账）
- 加 `--ignore-manifest`（旧的危险行为）：`缺失待下 34003` —— 会把 9611 张已下过的再下一遍
> `--ignore-manifest` 是逃生口：只有怀疑归档坏了、确实要全量重抓时才用（`--codes A,B` 隐含启用，
> 因为手动点名番号就是要重下）。

### 以后“只搬新的”怎么做
新封面都落在本站的 `assets/covers/` 里，所以要搬的永远是“目录里现在有的那些”：
```bash
python _script/manifest/covers_manifest.py scan               # 1) 登记新封面（下完一轮后跑）
python _script/manifest/covers_manifest.py list --present     # 2) 就是该搬去归档的新文件（带 --file 出文件名）
# 3) 拷完验完删掉本地新增；下次 scan 只会把这些记录改成 present=false（不删）
```
`list --since 2026-11-02` 可按“首次登记日期”筛，适合隔几天搬一次。

### 建站那边要注意的
- **`covers_manifest.json` 跟着仓库一起拷**：不然到了新地方又认为“没记录”而重下。
- 24 张老封面在盘上是**小写名**（`n0406.jpg` / `sky206.jpg` / `IBW-506z.jpg` 等），而 `movies.js` 的番号是大写。
  Windows 大小写不敏感所以 `file://` 直开没问题，但**Linux/区分大小写的服务上会找不到图**。
  现已修正：清单的 `file` 存**盘上实际名**，挂载 `local` 也写实际名（`covers_check.py` / `merge_collection.py` 都走
  `covers_manifest.true_name()`）。自己改名要小心：本项目沙盒会把 rename/replace 当危险操作中断。
- 封面目录如果只想换盘又不想改数据：用 junction（无需管理员）
  `mklink /J "D:\workspace\z_WEB-MOVIES-GALLERY\JAVDB\assets\covers" "E:\JAVDB_media\covers"`
  —— 但这样 `assets/covers/` 永远不为空，清单判定不会触发（两者都对，看你想不想在本地留全量）。

### 顺带修的三个既存问题（本次改动时发现）
1. **`assets/covers/` 里又积累了 95 张“假 .jpg”**（Cloudflare 拦截页 4.5KB）——上次 `--repair` 失败后把拦截页留在目录里，
   拦截页体积过了旧检查。现在：失败时如果盘上文件不是图片就标 `valid=false`（下次还能发现它），修好后重新登记为真。
2. **瞬时网络失败不该写进 `covers_unavailable.json`**：旧代码把所有失败都当终态，代理一断就把好片永久当死链。
   现在只把 **403/404/410（CDN 上确实没有）** 记为终态；超时/000/5xx 不记，重跑 `--repair` 会再试。
3. **stdout 被管道/重定向时 Windows 默认用 GBK**，日志里的 ✅/⚠ 会 `UnicodeEncodeError` 直接砸死进程
   （`move_check.py status | tail` 实遇）。已在 `covers_manifest.py`（被其他脚本 import）与 `move_check.py` 里
   `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`。

### 已知遗留（不影响“不重下”）
- `dl_rank_covers.py` / `top250_covers.py` / `rankings_spider.py` / `parse_build.py` / `merge_top250_into_movies.py`
  是首轮建站的一次性脚本（仍写死老机器路径 + 用体积判真伪），**别再拿来跑增量**；要重做封面一律用
  `covers_check.py` + `covers_manifest.py`。
- 清单里另有 82 张 `valid=false`（盘上仍是拦截页，其中大部分番号已记在 `covers_unavailable.json` 或不在 movies.js）；
  要彻底洗白就 `python _script/manifest/covers_check.py --repair --bad-only --retry-unavailable`。
- `assets/covers/` 里还有 259 个番号不在 `movies.js`（旧数据残留，`move_check.py status` 会列出）——跟着一起搬即可，不是错。

---

## 11. 早期会话记录精华（2026-10-10 ~ 10-12，原 `.workbuddy/memory/*.md`，已并入并删除）

前 10 节讲的是“下次怎么继续跑”；这一节补上“当初为什么这么设计”和“踩坑的具体代价”。

### 11.1 演进时间线
| 日期 | 干了什么 |
|---|---|
| 10-10 | 看过列表 → 本地瀑布流（先误判 2 页/40 部，二分探测修正为 **30 页/583 部/582 封面**）；同日建“想看”（44 页 871 部零缺图）；深夜跑通预告 HLS 下载管线；**`.jdb_chrome` profile + CDP 取 cookie 方案当天定型**（弃用 agent-browser：headed Chromium 随机崩、daemon 卡死） |
| 10-11 上午 | 数据拆分：`movies.js`（全集静态信息）+ `watched.js`（以后只维护看过与评分）；磁力库（1454 部 → 1452 有数据）；top250 榜单（7 页 250 条）+ 独立画廊 |
| 10-11 中午 | **key 从 id 换成规范化 code**（`rekey_to_code.py`，旧文件留成 `*.idkey.bak`）；5 星评分同步（当时只做了 star=5 共 59 部，star=1..5 全量是 11-01 才补的） |
| 10-11 下午 | **第一次目录重组**（`reorganize.py`）：`javdb_watched/ javdb_want/ javdb_top250/ javdb_videos/ javdb_data/` → 扁平成 `covers/ videos/ *.js + _backup/`；排行榜 7 分类扩展（6558 条）→ 用户要求只留 TOP250（全量存进 `_backup/top250_full_*.js`）；磁力补全 4659 部跑完 |
| 10-11 晚 | `watched.html` + `want.html` 合成 **mylist.html**（tab 切换）；`top250.js` 精简为 `{rank,code}`，影片信息下沉 movies.js（共 6097 部），页面用 `resolve()` 取详情 |
| 10-12 | 收藏的演員/系列爬虫 `collection_spider.py` + `collection.html` 上线；merge 改字段级去重；movies.js 14,913 → 33,910；**本 RESUME.md 创建** |
| 11-01 | 清单增量同步跑通（第 9 节）；封面假 .jpg 集体修复；播放字段与「只增不删」改正（第 8 节） |
| 11-02 | 封面已下载清单 `covers_manifest.py`（第 10 节）；目录整理成 assets/data/_script/_logs/_pipeline（第 2 节）；会话笔记并入本节 |

### 11.2 页面结构与解析姿势（实测，以后改正则先看这里）
- **收藏清单页**：演員 = `div.box.actor-box` + `id="actor-{slug}"` + `img.avatar`；
  系列 = `div.box` + `id="series-{slug}"` + `<strong>名</strong><span>(N)</span>`（N=影片数）。清单本身只 4 页。
- **总数来源**：演員页有 `<span class="section-meta">661 部影片</span>` 可直接算页数；
  **系列页没有**，只能拿清单里的 `(N)`。条目影片页分页控件会被截断 → 自己 `?page=N` 探到空页。
- **用户列表条目**：番号取 `data-batch-id="xxx"` 属性（`href`/`class="box"` 属性顺序不固定，别靠它们）。
- **磁力区**：切条目**必须用 `data-rank` 锚点**（嵌套 div 会让正则 lookahead 截断漏条目）；
  class 有 `item odd` / `item `（带空格）两种变体 → 正则写 `item[^"]*`；`tags` 只在 magnet-name 区内取否则会串到下一条；
  详情页 div 属性会换行 → 正则要 `re.S` + `\s+`；“此影片暫無網友分享磁鏈”是**真的没人发种**，不是误判。
- **键规范**：一律用规范化 code（strip + 压缩空白 + 大写）作 key，条目里保留 `id` 备用；
  手动改 watched.js 就写 `"FNS-253": 4.5`。历史上 28 个非标准 code（`n1343`、`legsjapan-1025`、`RED058`、
  `Milfs Like it Big` 这类欧美/素人条目）当 key 与文件名都没问题（无非法字符）；风险评估记在 10-11：
  JavDB 事后修正番号会让 code key 失联，但至今未发生。
- **读 `*.js` 数据文件的正确姿势**：不要用 `split("=", 1)`（文件顶部注释里就有 `=`，会炸）；
  用 `t.index("window.JAVDB_XXX = ")` 定位 + `json.JSONDecoder().raw_decode()`，或正则
  `window\.JAVDB_\w+\s*=\s*(\{.*\})\s*;\s*$`。
- 想看名单写盘时**必须用 movies.js 的规范化 key 本身**，不能用页面原始 `c.code`（有小写/含空格的会漏匹配）。

### 11.3 限速与并发参数（实测有效，别拍脑袋改）
- 详情页类（磁力/清单）：起始 **2.5s/请求**；连续成功每次 ×0.88 回落，下限 1.5s；响应 >8s 视为限流前兆 → ×1.6 抬高；
  真撞限流 → ×2.2 抬高（上限 25s）+ 冷却 60–180s；每 150 部主动长歇 15–30s。
  实测 ~2.6s/部（旧固定 8s/部，3 倍提速），4595 部全程只撞几次限流。
- 收藏爬虫：每 2 条存盘、每 10 条写 js；封面走直连 CDN 8 并发（`CVW`），磁力走代理只用 2 并发（`MGW`）。
- 预告片：默认 4 并发；`--workers 6` 会把单节点代理打满（120s 仅完成 1 条、单条 ~113s），收尾扫描降到 2–3。

### 11.4 验证方法的教训
- 图片是否可见要**截图/看 opacity**；`naturalWidth>0` 只证明加载不证明显示（坑 13）。
- 后台标签页会被 Chrome 节流不加载图片 → CDP 验证前先 `/json/activate` 切到前台。
- 多字符串 patch 一个文件（尤其 html）要**整体 diff / 通读验证**，不能只 grep 一处：10-11 重组 want.html 时
  先“替换静默失败”（原文件只有一个 script 标签，patch 假设有两个 → replace 无操作 → 页面卡“正在载入”），
  补了标签又漏了 `const WANT = window.JAVDB_WANT || [];` 声明，同一个坑踩两次。
- 循环里做 UI 验证要**每轮重新 querySelector**（拿重建前的旧按钮引用 click 会无效）。
- CDP 助手 `gj()` 要容错非 JSON 响应（“Target is closing”）；新版 Chrome 的 `/json/new` 必须用 **PUT**。
- cookie 抽不到时先查 9222 端口是不是被日常 Chrome 占用（占了就换 9223 开隔离实例）。

### 11.5 基线数字（用来判断数据有没有退化/丢过）
- **磁力（10-11 收官）**：movies 6097 / 有磁力 **6078（99.7%）** / 真无磁力 19 部 / 磁力总数 **91,779** 条；
  magnets.js 的 key 与 movies 完全对齐、孤儿 key 0。
  19 部真无磁力（不是抓取失败）：SPAY-748, SVSHA-021, FC2-1195420, MBDD-2020, LEGSJAPAN-1025, DFSLV-002,
  REBD-231, MMR-AA065, HEYDOUGA-4030-1606, MRMM-030, BOIE-023, POTA-013, XXX-AV-20437, MARD-003, CRPD-041,
  FC2-40318582, RBD-237, SEND-149, 030708_297。
- **预告（10-10/11）**：1454 部里 1053 有预告（看过 413 / 想看 640）；看过 413 → 最终 380 部落盘（7.0 GB），
  33 部源站失效；单部约 40–60MB、1–2 分钟。
- **清单**：看过 583（30 页）、想看 871（44 页）→ 现在 583 / 882（第 9 节）；收藏清单 演員 174→175、系列 121→122。
- **封面**：首轮 watched+want+top250 三源合并 1661 张（去重复用 43 张，那 43 张就在 `_backup/`）。

### 11.6 `_backup/` 里那些文件的来历对照
| 文件 | 来历 |
|---|---|
| `*.idkey.bak`（movies/watched/magnets） | 10-11 把 key 从 id 换成 code 前的原始文件 |
| `top250_full_items.js` / `top250_full_rankings.js` | 10-11 排行榜 7 分类版（6558 条）被砍到只留 TOP250 时的完整备份 |
| `index.html` / `want.html` / `watched.html` / `config.js.bak` | 10-11 重组前的旧页面；watched+want 后来合成 mylist.html |
| `*.before_users_sync` | `users_spider.py` 首次同步前的快照；**`covers_check.py --scope new` 仍靠它算新片** |
| `movies_before_media_fields.js` | `media_scan.py` 首次写字段前的快照，是 `restore --from` 的恢复源 |
| `movies_before_collection.js` / `_talks.js` / `_merge.js` / `movies.js` | 更早几轮合并/字段改写的快照（现无代码依赖） |
| `movies_before_assets_layout.js` | 11-02 目录整理前快照（里面还是 `covers/` 旧前缀） |
| `collection_ckpt.paused_*.json` / `collection_ckpt_before_20261101.json` / `magnets_ckpt.*` | “先 cp 断点到 _backup 再 taskkill”规矩留下的现场（坑 11） |
| 43 张 `*.jpg` | 首轮三源合并时挑下来的真图片。**现在已无用**：`merge_collection.py` 虽有“`assets/covers/` 没图就从 `_backup/` 拷”的兜底分支，但它的前提是“清单里没记录”，而这 43 个 code 全部已在 `covers_manifest.json` 登为已下载（图本体在搬走的归档里），所以该分支一次也不会触发 —— 确认后可删（`rm -f _backup/*.jpg`，6.4 MB） |

### 11.7 当时留下的其它细节
- 最初页面样式参考的是另一个本地项目（`9dy电影` 的深色画廊）；卡片一度是直接 `target=_blank` 跳 JavDB，
  后来才改成弹层（用户明确要求：**无本地视频就不渲染视频 DOM**）。
- 数据格式铁规：`window.SITE_CONFIG = {...}` 这种全局赋值必须有，`const` 声明在浏览器不挂 window（页面读不到）。
- 登录账号密码存在 `_pipeline/javdb_login.txt`，供 cookie 失效时 `autologin.js`/`refresh_cookie.js` 自动重登。

