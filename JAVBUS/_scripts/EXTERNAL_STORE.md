# 图片存放 / 移动硬盘归档手册

`assets/images/` 是本项目唯一的大头（历史 4136 张约 4.6 GB，其余全部文件加起来 ~180 MB）。

## 目录结构（2026-11-02 整理定，根目录只留 html 文件与目录；路径单一来源是 `_scripts/common/paths.py`）

```
JAVBUS/
├── index.html                 # 打开就是站（画廊 + 帖子详情同一文件，靠 #tid= 路由）
├── assets/images/<tid>/*.jpg  # 图片字节（唯一会被搬去移动硬盘的大头）
├── data/                      # 交付层：data.js（列表）+ posts/shard_<i>.js（详情按 tid%SHARD_N 分片，点开某帖才注入它所在那一片）+ imgsrc.js（本地路径→原始图片 URL 反查表，供页面切换图片来源），build.py 生成
├── _rawdata/                  # 原始抓取件：list_pN.html + posts/<tid>_pN.html，删了要重爬
├── _pipeline/                 # 过程与台账：parsed/、index.json 累积库、imgmap.json 台账、
│   │                          # crawl_done.txt 断点、last_new.txt、cookie.txt、
│   │                          # store_state.json —— 删了丢数据/丢断点
│   └── baselines/             # guard.py 的回归快照（pre / post），用来证明本轮只新增（操作件，不是备份）
├── _backup/                   # 快照备份，**按日期建子目录** _backup/YYYYMMDD/（inc/backup.py）
│   ├── 20261103/              # 当日快照：index.json、imgmap.json、crawl_done.txt、data.js、
│   │                          # index.html、store_config.json + MANIFEST.json（账号数、时间）
│   └── 20261031/              # 手工归档（无 MANIFEST，prune 永远不碰）：旧 v1 基准目录
├── _logs/                     # 纯 informational：文件名强制带 YYYYMMDD_ 前缀
│   │                          # *.log、img_fail.txt、guard_history.log
│   └── shots/                 # 验证用截图
└── _scripts/                  # 分区：common/（paths、discuz、pannet、spider_kit）、
                               # inc/（update、crawl、parse、images、guard、backup）、
                               # site/（build、validate）、store/（external_store、store_config.json）
```

### 快照备份 `_backup/YYYYMMDD/`（2026-11-03 定）

所有**备份文件**只往这里放，一天一个目录（同日多次 `snap` 幂等覆盖，内容相同不重写）：

```bat
python _scripts\inc\backup.py snap             :: 写库前留可回退点（update.py 每轮自动跑）
python _scripts\inc\backup.py list             :: 看有哪些日子、多大、当时多少帖/台账/断点
python _scripts\inc\backup.py prune --keep=14  :: 只留最近 14 天（update.py 每轮自动跑）
```

备的是“删了就得重爬/重算”那六件（约 0.8 MB/天）：`index.json`、`imgmap.json`、`crawl_done.txt`、
`data/data.js`、`index.html`、`store_config.json`。回退就手工 `copy _backup\<日子>\index.json _pipeline\index.json`。
护栏：prune 只动“目录名是 8 位数字 **且** 里面有本工具写的 `MANIFEST.json`”的日子目录；
删除只用 `os.remove` + `os.rmdir`（非空即失败），全程无 rmtree——所以手工归档（如 `20261031/`）永远安全。
`_pipeline/index.json.bak` 这种就地备份已取消（统一归 `_backup/`，避免两套命名）；`_pipeline/baselines/`
是 guard 的操作快照不是备份，故不搬。

**`_logs/` 整个目录可以随便删，不丢任何数据**（脚本不会往这里写重要东西；`img_fail.txt` 当日重跑会重写）。
**命名铁规**（2026-11-03）：`_logs/`（含 `shots/`）里的文件名必须带 `YYYYMMDD_` 日期前缀——脚本产出由
`paths.dated()` 自动加，你手工重定向请写 `> _logs/$(date +%Y%m%d)_update.log`；`update.py` 每轮开头会把
没带前缀的文件列出来提醒（只提醒不阻断）。
引用约定：交付层里的本地图片一律写成相对站根的 `assets/images/<tid>/<文件名>`；台账在 `_pipeline/imgmap.json`，
它是"这张图曾成功落盘"的唯一事实源。

## 0. 当前分工（2026-10-30 定，覆盖本文其余章节里的旧自动外迁描述）

| 谁 | 做什么 |
|---|---|
| 增量更新（`update.py`） | 刷 10 页列表 → 只抓新帖 → parse → **补抓"帖内引用"（`crawl.py refs` 逐层闭包，默认最多 `--ref-depth 10` 层，收敛就停；每层内部自动补跑 parse）** → 新帖图片落本机（`assets/images/` 不存在就自动重建）→ build → validate。到此为止。 |
| 你（人工） | 把整份 `JAVBUS` 复制到移动硬盘的**站点目录**（覆盖，硬盘保留全量图片）→ 确认后删本机 `assets/images/` → 下一轮照常从"图片目录不存在"开始。硬盘侧的图片必须在**站点内的 `assets\images\<tid>\`**，不是硬盘根上的 `images\`（否则硬盘那份站点旧图全破，2026-11-03 定） |

`store_config.json` 的 `mode.images = "local"`；`external_store.py` 的 `sync / move-images / migrate /
junction / bring-back` 属旧的自动外迁流程，**日常增量不再调用**（`migrate` 在 `local` 策略下会直接拒绝执行）。
这些命令本身还在，需要时手动用；下面 §1—§2、§4 讲的是它们。

## 1. 策略项（`_scripts/store/store_config.json`）

| 口径 | 配置项 | 当前值 | 含义 |
|---|---|---|---|
| 图片落哪儿 | `mode.images` | `local` | 新图只写本机 `assets/images/`；复制到移动硬盘由人工做 |
| 移动硬盘目标路径 | `hdd_root` / `hdd_project_subdir` | 空（未绑定） | 只有手动跑 `init-target` 后，`status/verify` 才报得出"仅在硬盘"分层 |
| 图片范围 | `scope.images_dir` / `scope.image_exts` | `assets/images` + 6 种后缀 + 魔术字节 | 只认真正的图片；图片目录里混进的非图片不搬不删 |
| 台账 | `_pipeline/imgmap.json` | 6467 条 | **永久台账**：条目代表"这张图曾成功落盘"，本机字节被删后不清除（见 §5.8）。同一张图被第二个帖引用时直接**复用旧路径**，不重下、不改写映射（2026-11-03 修） |
| 硬盘侧 prune | `prune_hdd` | `false` | 工具永不删硬盘文件 |
| 校验 | `validate.py` | 见 §6 | 本机可见 / 已人工外迁(台账在案) 都可接受；台账外且两处都无才 FAIL |

`mode.make_junction` 已置 `false`：不再建目录联接。历史遗留的联接会被写入闸门拦下（rc=4），
按提示 `junction remove` 摘掉即可（只摘联接、不删字节）。

## 2. 旧的自动外迁流程（已停用，留作参考）

```bat
python _scripts\store\external_store.py init-target --hdd=E:\JAVBUS_ARCHIVE
python _scripts\store\external_store.py plan        :: 只读：将拷/搬多少文件、多少字节
python _scripts\store\external_store.py migrate     :: 拷非图片 -> 搬图片 -> 建联接 -> 校验（local 策略下拒绝执行）
```

## 3. 日常增量（今后每轮就这么跑）

```bat
python _scripts\inc\update.py
```

流程：刷 10 页列表 → `guard.py snap pre` 拍基准 → 只抓"列表 − crawl_done"的新帖详情(前5页) →
parse 累积（新帖插最前、旧帖原样不动）→ **只下本轮新帖的图**（`images.py all --only=<新增tid>`，
落本机 `assets/images/`）→ build → validate → `guard.py snap post` + `diff pre post`（历史没被动过才算收尾安全）。

**写入闸门（现在的语义）**：`update.py` 开跑前执行 `external_store.py write-guard`：
`assets/images/` 是真实目录 → 放行；不存在 → **重建空目录后放行**（上一轮你复制走并删掉了，这是正常起点，
不再当成拔盘故障硬停）；还残留旧策略的目录联接 → rc=4 停住，让你先 `junction remove`；目录建不出来
（磁盘/权限）→ rc=5 停住。cookie 失效仍按原样 rc=2 停止。

跑完 `update.py` 会打印一句收尾提醒：请把整份 `JAVBUS` 复制到移动硬盘、再删本机 `assets/images/`。
删之前想确认，可以跑 `python _scripts\site\validate.py`（看"本机可见/已外迁"两档数字）和
`python -c "import glob;print(len(glob.glob('assets/images/*/*')))"`。你手工重定向的 `*.log` 请放 `_logs/`，
验证用截图放 `_logs/shots/`（这两个地方删光也不丢数据，且不参与硬盘同步）。

### 3.1 硬盘侧的落位（口径 2026-11-03 定：硬盘也是一份完整可打开的站点）

交付层引用的是相对站根的 `assets/images/<tid>/<文件名>`，所以硬盘站点必须满足：

```
<盘>:\JAVBUS\                 ← 站点目录（拷过去的）
├─ index.html  data\  assets\  _rawdata\  _pipeline\  _scripts\
└─ assets\images\<tid>\*.jpg  ← 全量 4136 张在这，不是硬盘根上的 images\
```

**一次性归位**（现状：硬盘上只有一个 `images\` 仓库，内部已是 `<tid>\文件名`，只是缺 `assets\` 这一层）：

```bat
:: 1) 同卷 move 只改目录项，秒完成，不拷 4.6GB
mkdir E:\JAVBUS\assets
move  E:\旧位置\images  E:\JAVBUS\assets\images

:: 2) 本机站点整份覆盖过去（本机已无图，这一步碰不到硬盘 assets\images 里的已有文件）
robocopy D:\workspace\z_WEB-MOVIES-GALLERY\JAVBUS E:\JAVBUS /E /XD __pycache__

:: 3) 核数量：硬盘图片数应等于台账条数（现 4136）
for /f %c in ('dir /s /b E:\JAVBUS\assets\images\*.* ^| find /c /v ""') do @echo 硬盘图片=%c
python -c "import json,io;print('台账条目=',len(json.load(io.open('_pipeline/imgmap.json',encoding='utf-8'))))"
```

注意事项：
- **绝对不要用 `/MIR` 或 `/PURGE`**：那是拿本机的增量去镜像硬盘的全量，会把硬盘上本机没有的历史图片删光。
- `robocopy` 退出码 0–7 都算成功（1 = 拷了文件），≥ 8 才是真错，别被"非 0"误判。
- 插盘验证：双击 `E:\JAVBUS\index.html`，开一个历史帖（如 `#tid=176915`）旧图应该完整显示。
- 想让 `external_store.py status/verify` 能报"仅在硬盘 N 张"，得插着盘跑
  `python _scripts\store\external_store.py init-target --hdd=E:\JAVBUS_ARCHIVE`（`hdd_project_subdir` 默认 `site`，
  需与硬盘上实际站点目录名一致）；没绑定时这两项只报本机情况，不影响 `validate.py` 结论。

## 4. 拔盘 / 换盘 / 回搬

```bat
python _scripts\store\external_store.py status              :: 现在图片在哪、硬盘在不在位、有无漂移
python _scripts\store\external_store.py verify              :: 随时复查（只读）
python _scripts\store\external_store.py write-guard         :: 抓取前闸门：残留联接返回 4，目录建不出来返回 5，否则 0（并保证本机 assets/images/ 存在）
python _scripts\store\external_store.py bring-back          :: 从硬盘取回图片（默认硬盘副本保留；--hdd-delete 才移回）
python _scripts\store\external_store.py junction remove     :: 只摘联接点（cmd rmdir，不跟随删内容）
python _scripts\store\external_store.py junction create     :: 重建联接
```

换了个盘/换了盘符：重跑 `init-target`，卷序列号不一致时所有写操作会先拒绝，等你确认。

## 5. 安全护栏（写在代码里，不靠人小心）

1. **认盘标记**：目标卷上必须有 `init-target` 写的 `JAVBUS_STORE.json`，且卷序列号与登记一致，否则拒绝 —— 防止插错盘把别的盘当归档盘。标记属于别的项目也拒绝。
2. **跨卷校验**：目标卷必须不同于项目所在卷；同卷只有显式 `--allow-same-volume`（演练）才放行，并在 `status`/每次输出里报警。
3. **只碰图片目录**：搬删前逐文件检查（路径在 `scope.images_dir`（默认 `assets/images/`）内 + 后缀白名单 + 魔术字节确实是图片）。图片目录里混进的 `.txt`、伪装成 `.jpeg` 的文本一律不搬不删（自测覆盖）。
4. **先校验后删源**：拷到硬盘 → 目标端大小 + sha256 与源一致 → 才 `os.remove` 本机源；任何一步不过就保留本机文件。目标已存在同名同尺寸但内容不同时**拒绝覆盖**（不砸硬盘上既有数据）。
5. **无递归删除**：全程没有 `rmtree`；清理空目录只用 `os.rmdir`（非空即失败）；`junction remove` 只摘联接，对真实的图片目录直接拒绝。
6. **容量预检**：按 `min_free_ratio`（默认 105%）估算，空间不够就在动手前停下。
7. **断点续跑**：按 (相对路径, 大小, mtime) 记账在 `_pipeline/store_state.json`，中断重跑不重复拷贝/哈希；`store_state.json` 每轮单独推到硬盘。
8. **台账不被本机字节牵着走**：`images.py` 收尾只校验"本轮下过的 URL"，历史条目一律保留。你要是哪天
   让它在"本机图片已被删"的状态下按存在性过滤，几千条映射会被清空，`build.py` 于是把所有旧文章的
   图片退回远程 URL —— 离线库当场退化，还会随下一轮整份复制覆盖掉硬盘里的正确版本。

## 6. 校验口径（`validate.py`）

本地图片引用按层给出数字并归因：

- **本机**：字节在项目 `assets/images/` 下（本轮刚下的，或你还没复制删走的）
- **经联接点在硬盘**：本机可达但 `assets/images` 是旧联接（新流程不该再出现）
- **仅在硬盘**：本机看不到，`hdd_root` 已绑定且盘插着
- **已人工外迁**：本机看不到、也没绑硬盘，但 `_pipeline/imgmap.json` 台账在案 → 你复制走并删了本机图片目录，**判可接受**
- **两处都无**：台账里也没有 → 唯一 FAIL 情形
- 另外单独复核**本轮新增帖**：本机可见 = 本轮已落盘；看不到但台账在案 = 你已经做完人工收尾（整份复制走 + 删图片目录），同样可接受；两处都没有才是本轮真没落盘

`validate.py` 还继续管：交付层引用总数、`forum.javcdn` 残留、JS 语法检查（一个 node 进程用 vm.Script 批量编译，
426 个文件 1.5 秒；node 不可用时自动回落逐文件 `node --check`，语义不变）。

## 7. 自测与回归

### 7.1 回归基准（每轮自动跑，`_scripts/inc/guard.py`）
`validate.py` 管"图片字节在不在 + JS 语法"，`guard.py` 管另一件更要紧的事：**本轮有没有动到历史**。

```bat
python _scripts\inc\guard.py snap pre          :: update.py 在开始写库前自动拍
python _scripts\inc\guard.py snap post         :: 收尾自动拍
python _scripts\inc\guard.py diff pre post     :: 自动比对，不过就把收尾提醒压下去
```

快照落 `_pipeline/baselines/<label>.json`（约 260 KB/份，只留 pre/post 两份），结论另存一行流水到
`_logs/<YYYYMMDD>_guard_history.log`（按日分文件）。判 FAIL 的八条：旧帖消失 / 被改写 / 相对顺序被打乱；imgmap 台账缩水或
丢 key；台账 value 不再是 `assets/images/` 前缀（= 离线库退回远程 URL）；`_rawdata/posts`、`parsed`、`list_pN.html` 文件数减少，
`data/posts` 详情分片内帖数减少（分片改造后文件数恒定，改判 tid 行数）；crawl_done 与 index 对称差不为空。`assets/images` 本机字节减少
**只报告不判失败**（那是你人工复制走 + 删本机图片的预期结果）。

旧口径快照（v1：`_src`/根 `data.js`/`images/`）与新口径不可互比，diff 会直接拒绝并提示；原项目外
`D:\workspace\_JAVBUS_BASELINE_20261031` 已归进 `_backup/20261031/legacy_baseline/`（含 10-31
的三份 v1 快照、files_before 实物备份、两轮 run 日志、当时的 chk_*.py），确认无用可直接删那个子目录。

### 7.2 旧的 external_store 自测套件（已不存在）
`external_store.py` 当年那套 mock 全流程 + 护栏用例（71 条）写在
`D:\workspace\_JAVBUS_BASELINE_20261030\selftest_store.py`，该目录**已被删除、无法重跑**。它覆盖的是
已停用的自动外迁流程（`migrate/move-images/junction/bring-back`）；日常 `local` 策略只用到 `write-guard`，
其两条判定（残留联接 rc=4、缺目录自动重建 rc=0）本轮已在真机上实跑验证过。
