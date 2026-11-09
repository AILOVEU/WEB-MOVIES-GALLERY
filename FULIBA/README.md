# FULIBA 离线归档站

`fuliba2023.net`（福利吧）全站离线归档：文章正文、评论树、正文图片全部本地化，可断网直接浏览。

## 1. 现状

| 项 | 数值 |
|---|---|
| 文章 | **7243 篇**（2011 — 2026，28 个分类；促销清理 v9 删 209 篇） |
| 评论 | **185557 条**（含楼层/回复树/点赞数） |
| 正文图片 | **143868 张 / 32.79 GB**（jpg/gif/webp/png；具体分类计数见 `_logs/<日期>/report.md`）—— 历史库在**另一台电脑**上，本机 `assets/images/` 只存增量新图（§13.2） |
| 其中分页子页 | 1446 篇文章带内部分页（共 2302 个子页） |
| 单篇静态页 | 7243 个（`data/<年>/article/<url_key>.html`，一篇一文件） |
| 交付自检 | 正文图片引用 **138761 处、全部带本地路径**：已落到本机 `assets/images/`（2026-10-31 之后新增的那些，当前 414 张），138347 处指向另一台电脑上的历史库（自检计为「在外部库·未评估」，不计丢失）；6615 张从未取到的死链图已按 2026-10-29 决定**全站清除原文地址**（`s04s_purge_dead.py`，占位图不再回显原址）；映射缺失 0 / 0 字节 0 / 死链地址残留 0 |

> 2026-10-31 **年度化改造**（用户要求，详见 §2 目录树与 §14）：项目改成**一年一个自包含目录**。
> 原料从「每篇 2~4 个文件」（`_rawdata/html` + `_pipeline/parsed` + `_pipeline/subpages`，共 16,756 个）合并为
> **每篇一个 JSONL 容器** `<年>/material/<url_key>.jsonl`；单篇页从根 `articles/` 搬进年目录（现为 `<年>/article/<key>.html`，深度仍是 2 层，
> 页内 `../../images/…` 与 `../../assets/…` 一个字没改）；全局 `data.js`（5.6MB）退役，改为各年 `<年>/data.js`
> 自注册 + `index.html` 预枚举 2011..2050+undated 运行时合并（新增年份只建目录，不改任何代码）；
> `_pipeline/articles.json`（350MB 汇总）停产。转换由 `s07_migrate_years.py` 完成并逐字节抽样校验通过；
> 旧目录（`_rawdata/html|parsed|subpages`、根 `articles/`、旧 `data.js`、`articles.json`）先同卷改名到
> `_pipeline/_old_layout_20261031/`，**2026-10-31 结项验收后已删除**（共 23,989 个文件 / 2.29 GB）。
> 从此年度容器就是这些原料的唯一存放处，别再指望旧布局回退。
> 非图文件数：全树 **28,807 → 15,747**；目录数 **8,267 → 88**
> （单篇页平铺为 `<年>/article/<key>.html`，删掉 7,231 个只装一个文件的目录；
> `_rawdata/pages/` 从 256 个哈希桶改成分区名；原料目录改名 `material/`）；
> 其中「交付+原料」核心集 **14,538**（容器 7,231 + 单篇页 7,231 + 年度 data.js 16 + 根产物/代码 ≈ 60）；
> 另：列表页缓存从 4,486 个瘦到 970 个 —— 其中 **3,467 个是同一页**
> （canonical 均为 `/dytj/page/40`，当年 --deep 后期站点把同一内容回给所有后续请求，旧代码无校验全存了），
> 现已清除并在写缓存处加了 `looks_like_list()` 拦截；每年新增 `_audit.json` 做拷贝后验收。
> 且全站重建已做到**内容未变零写入**（实测 s06 二次运行：写盘 0 / 跳过 7,252），
> 所以一轮增量的迁移集就是「当年目录里新增的那几个文件」。
>
> 2026-10-31 **图片层口径定为 `mode=incremental`**（用户决定）：历史 images（143868 张 / 32.79 GB）已被移到另一台电脑，
> 以后**只负责新增图片的下载**，按原命名规则落进项目内 `assets/images/`（真实目录）；历史图一律认为「已在库里」：
> `s04i` 改按「manifest 有条目 = 已下载」判定（不重下）、`s04f` 不因本机缺文件删台账（保留 140873 条）、
> `s04d` 把 138355 处历史引用原样保留（不回退远程地址、不固化占位图）、`s04j/s04r` 在本模式直接拒跑（防止把 13.8 万张当死链重下）。
> **页面必须能正常打开、可以没图**：`s06` 生成 `assets/img-fallback.js`（单篇页 head 引入），在浏览器里把加载失败的图换成
> `assets/img-placeholder.png`（title 留原引用地址），不改任何数据；库接回来无需改动即自动恢复真图。
> 实测：旧文章页 38 个 `<img>` 全部渲染、0 张破图图标（6 张缺图换为占位图）、无 JS 异常；新增 2 图（81.5 KB + 131.4 KB，魔数 FFD8FF）已在本机。

> 2026-11-06 **站点根清空**（用户要求：这两个文件不要放到根目录）：根目录 `py.cmd` 转发壳**已删除**，
> `PROMO_REVIEW.md` **移入** `_pipeline\promo_review.md`（内容一字未改，只在开头加了位置说明）。
> 根目录现在只剩 `README.md` / `index.html` / `archived.html` + `assets\` + `data\`，其余全是下划线工作台目录
> —— 与 `common.py` 开头那句「根目录只留站点外壳与资源」对齐了。
> **命令写法全线改成带前缀的形式** `_scripts\py.cmd _scripts\<组>\<脚本>.py`：一次性改写工具（放在项目外）
> 批量改 **98 处**（README 36、`_scripts\README.md` 4、`_backup\README.md` 1、12 个脚本文件 57），
> 另手工改写 17 处叙述性文字（README 目录树与 §3/§6/§11、`_scripts\README.md` 开头、`_pipeline\README.md`
> 位置树与清理指引、`_scripts\py.cmd` 头注释、`s06_build_site.py` 的 archived.html 模板、`rebuild.py` 设计要点）。
> 裸 `py.cmd` 从此在根目录敲不通（cmd.exe 只在当前目录找同名命令）；真身 `_scripts\py.cmd` 的逻辑一字未动。
> 需要还原那个壳的话，全文就是三行：`call "%~dp0_scripts\py.cmd" %*` + `set "RC=%ERRORLEVEL%"` + `exit /b %RC%`
> —— 本仓库没有任何 git 提交、也不跟踪这两个文件，`_backup\20261104_dir_restructure\scripts_before\py.cmd`
> 存的是重构**前还在根目录的真身**（41 行完整版），不是这个壳，所以这里把壳的正文记进文档以免彻底丢失。
> `archived.html` 不再指向项目根文档：模板改为「名录就是本页这份清单；逐篇判定依据见台账
> `_pipeline/skipped_promo.json` 的 `source` 字段，防抓回登记见 `_pipeline/urls_gone.json`」，
> 站点本体不依赖任何工作台文件（原来那句「见项目根目录 PROMO_REVIEW.md」正是把文档写在站点里的后果）。
> 校验：改写后复跑工具 `--dry` 得 **0 处**（幂等、无遗漏）；27 个脚本 `py_compile` **语法错误 0**；
> 文档里 12 种入口调用指向的目标脚本**全部存在**；站点发布层（`index.html`/`data`/`assets`/`archived.html`）
> 对 `PROMO_REVIEW.md` 与裸 `py.cmd` 的引用均为 **0**；`_backup` 历史快照**未被改写**（仍保留旧写法）。
> 实跑 `_scripts\py.cmd _scripts\build\rebuild.py`：s06 写盘 **1** 个文件（就是 archived.html）/ 内容未变跳过 7279、
> `_verify` 全树「结论: 计数一致」（7243 篇 / 414 图 / 0 字节 0），总耗时 117.8s；
> mtime 改动面独立核对 = **19 个文件**，与上面列出的预期集合一一对应，`index.html` 与 `data\`、`assets\` 零改动。
>
> 2026-11-06 **增量更新**（`update.py`，未用 `--refresh`）：新增 **3 篇**，全部落 `data/2026/`——
> `2026155`「2026年福利汇总第155期：寒意来袭」（正文 100 图 / 14 评 / 1 个内部分页子页，发布 11-05）、
> `hghub`「HGhub：黄果短剧第三方客户端」（2 图 / 4 评）、`rsyc4`「纪录片《人生一串》第四季开更」（1 图 / 1 评）。
> **本轮 sitemap 通道零新增**：`s01` 拉 665 条 → 合并 7252 → 按 `urls_gone` 剔除 12 → urls **7240**（与基线一字不差）；
> 三篇全部由**分页发现通道** `s01b`（quick 扫 29 页、失败 0）带出 —— 发现来源 `/`、`/公告/`、`/推荐/`、`/网站推荐/`、
> `/utilities/`、`/dytj/`，`meta.source=pages`、`lastmod` 为空，合并后 urls **7243**。
> 这就是第 5 条设计要点说的「两套通道互不包含」，只跑 s01 会整批漏掉。
> `s02` 抓 3 跳过 0 失败 0（Clash 7890 在线，无 10054）、`s02b` 新抓子页 **1**（全树累计 2302）、`s03` 解析 3 落年 `[('2026', 3)]`；
> `s04i` 待下载 **103** 张 = manifest 里没有的新图，**成功 103 / 失败 0 / 跳过 0**（24s，本机 images 311 → **414**）；
> `s04f` 新增 0 重指向 0 清理 0（外部库条目保留 140873，manifest 有本地文件 141184 → **141287**）、
> `s04d` 范围模式改写 **3 篇**（其余 7240 篇未读未写、mtime 不动）：正文图本地化 103 处 / 头像 19 处 / 历史引用原样保留 0 处；
> `s06` 写盘 **6** 个文件 / 内容未变跳过 7274（迁移集 = 3 个单篇页 + 当年 `data.js` + `_audit.json` 等）；
> `_verify` 全树：覆盖率 7243/7243 **缺失 0**、正文图片引用 **138761 处**（映射有但文件缺失 0 / 0 字节 0 / 在外部库 138347）、
> 抽样 30 篇本地路径破图 **0**、评论 185557 条孤儿 0。总计 **223.8s**（s01 2 / s01b 47 / s02 4 / s02b 2 / s03 1 / s04i 25 / s04l 1 / s04f 4 / s04d 2 / s06 51 / verify 59）。
> 回报见 `_logs/20261106/update_report.txt`、`verify_report.txt`。
> **独立基准复核**（项目外临时脚本，不复用流水线计数）：逐篇重开容器读 `meta`+`parsed`、并把单篇页里的
> `../../../assets/images/…` 引用按页面真实位置解析到磁盘 —— 3 篇 **103 张图全部在盘（缺失 0 / 0 字节 0）**、
> 页面引用 206 处 **broken 0**；全树逐容器汇总 = 带分页 **1446** 篇 / 子页 **2302** / 评论 **185557** / 正文图引用 **138761**，
> 与 `_verify` 逐项对上，本文顶部现状表按此更新。
>
> 2026-11-04 **流水线改成范围模式 + 修一个既有图片键缺陷**：增量轮以前不管新增几篇都要
> 把全库重读一遍（本轮 614 秒里约 480 秒是这种「为了不动它们而读它们」），现在各步骤接
> `--keys-file=`、建站接 `--years=`，1 篇的一轮实测 **106~158 秒**（含不可省的两个发现通道限速
> 46~49 秒与全树自检 34~59 秒）；不带该参数仍是全树（详见 §5 第 12 条）。
> 顺手修了一个目录重构留下的坑：图片台账键被写成 `assets/images/` 前缀，打断 `s04d` 幂等判定
> （§8 第 1.1 条与 §10）。
>
> 2026-11-04 **增量更新**（`update.py`，未用 `--refresh`）：新增 **8 篇**（`2026153`/`2026154` 两期福利汇总、
> `bqzxbxd`、`coserslove`、`ltxlibray`、`mimk-289`、`tongjiang`、`xinyuanquan`），全部落 `data/2026/`。
> `s01` sitemap 665 条 → 合并 7252 → 剔除 `urls_gone` 12 → urls **7240**；`s01b` quick 扫 29 页、失败 0、新发现 0
> （`tongjiang`、`xinyuanquan` 是上一轮 `--dry` 的分页通道发现，`urls_pages_pending` 待补已在本轮清零）；
> `s02` 抓 8 跳过 7232 失败 0（Clash 7890 在线，无 10054）、`s02b` 新抓子页 **5** 个（累计 2301）、`s03` 解析 8 跳过 7232；
> `s04i` 待下载 **279** 张（= manifest 里没有的新图，无用重试 0）**成功 279 / 失败 0 / 跳过 0**、
> `s04f` 新增 0 清理 0（外部库条目保留 140873 → manifest **141184**）、`s04d` 改写 8 篇（另 7232 篇**零写入**）
> 本地化 280 处 / 历史引用原样保留 138355 处；`s06` 写盘 **11** 个文件 / 内容未变跳过 7266；`_verify` 破图 0、
> 8 篇正文图引用逐张核验**缺失 0 / 0 字节 0 / 魔数全部为合法 GIF87a-GIF89a-JPEG**；总计 614s。
> 回报见 `_logs/20261104/update_report.txt`。
>
> 2026-10-31 **增量更新**（`update.py`，未用 `--refresh`）：新增 **1 篇** `zgxybwg.html`「中国香烟博物馆：烟草查询小工具」（正文 2 图 + 1 条评论）。
> `s01` 拉 sitemap 7420 条 → 与清单合并 7441 → 按 `urls_gone` 剔除 210 → urls **7231**；`s01b` quick 扫 29 页、失败 0、本次零新发现；
> `s02` 抓 1 跳过 7230、`s03` 解析 1 跳过 7230（旧 7230 篇原料零改动）；`s02b` 子页累计 2294 无新增；manifest 140873 **零变化**、本机零图片下载。
> **首跑把这篇报成抓取失败**：Clash `127.0.0.1:7890` 没开 → 自动降级直连，而直连对文章页给了 `10054` 连接重置（同一时刻列表页 `/fuli/` 却 200）；
> 单独重试同一 URL 立即 200 / 145 KB —— 属**我方链路瞬时故障**，不是站点 404，重跑 `update.py`（`s02` 幂等只补缺那一篇）即落地。
> 总计 200s（s02 65s 多数在按限速等待）。图片库仍 `detached` → `s04i/s04l/s04f/s04d` 整组跳过，
> 该篇 2 张正文图与 1 个评论头像暂按远程地址显示，库挂载后 `update.py --force` 自动补齐。
>
> 2026-10-30 **增量更新**（`update.py`，只新增未用 `--refresh`）：新增 **2 篇**（`2026152.html` 四页福利汇总 + `kaobeiquguangg.html`），
> 正文图 **137 张 / 16.56 MB 全部落地（0 失败）**，含 `2026152` 的 3 个内部分页子页；`s02` 抓 2 跳过 7228、`s03` 解析 2 跳过 7228，
> 旧 7228 篇的原始 HTML 缓存 **mtime 变新 0 个**（原料层零改动），images/ 只增不减（143731→143868 精确 +137）。
> **`s04i` 待下载恰好 137 条**（无用重试 0）—— 2026-10-29 死链地址清除的效果在本轮得到验证。
> 独立基准复核（外置脚本，现拉 RSS 20 条 + 新鲜 2026-08/09/10 三月 sitemap 分片 214 条 + 首页/第 2 页 116 条）
> 与台账差集：真缺 **0**（新鲜 10 月分片 lastmod 已到 2026-10-30T06:53:58）；台账里 parsed 未落地 **0**。
>
> 2026-10-30 同日 **图片库已移出项目**（搬运由用户手工完成，项目内不提供搬运工具）：`images/` 的
> 143868 个文件 / 32.79 GB 现在 FULIBA 之外，项目内没有独立 images 目录（2026-11-04 起图片库在 `assets\images\`）；`_pipeline\images_store.json`
> 记为 `mode=external`、`external_root=null`（实际位置待回填）。此后 `update.py --force` 一轮实测：
> `[图片库] state=detached` → 跳过 `s04i/s04l/s04f/s04d`，正文/分页/解析/建站/自检全部照常
> （urls 7230 / html 7230 / parsed 7230 / articles 7230 / data.js 7230），**manifest 140873 条零变化、
> 本机零图片下载**，且 `common.py` 在 external 模式下不再凭空创建真实 images 目录。
> 自检里图片项显示「未评估」而非「丢失」，上一次「138347/138347 = 100%」是 08:18 图片还在本机时的结论。
> 恢复本机看图：把 `external_root` 填成 images 现在的位置 → `_scripts\py.cmd _scripts\store.py --status`
> 会自动建 junction（§13）。
> 2026-10-30 **图片搬运工具已退出本项目**：搬运（复制到移动硬盘 / 剪切到别处）由用户自己完成，
> 项目内不再有分片复制、校验、删源、站点副本那套执行器，`store.py` 只剩「库在哪」与
> 「跑增量时能不能动图片层」两个职责（§13）。

> 2026-10-26 **促销清理**（评审见 `_pipeline\promo_review.md`）：规则历经 v3→v9 共 9 版（整类豁免/强资源
> 保护/子串边界/硬证据门槛/人工复核），用户最终确认 **T0-T2 全量 204 篇 + 人工复核 5 篇删除**；
> 资源彻底清除（专属图片比对共用后删除 + html/parsed/单篇页），**合计释放 118.06 MB**
> （图片 933 文件 81.05 MB + 数据 37.01 MB）；全部 209 篇名录见站内「归档」按钮 → `archived.html`
> （由 s06 从 `skipped_promo.json` 生成），并登记 `urls_gone.json` 防止增量更新抓回。
> 同日上线**归档名单闭环**（`_scripts/audit/archive.py` + s06 改造）：首页卡片「归档」/管理页「移出归档」
> 标记存 localStorage（导航栏带待办角标）；名单确认后执行 `_scripts\py.cmd _scripts\audit\archive.py --apply`
> （移入=删资源并登记台账/防抓回，移出=重抓 `s02→s03→s04i→s04l→s04f→s04d→s06`，失败自动回滚登记；
> 也支持 `--import <archive_ops.json>` 手工喂名单）。
> 2026-10-27 **死链终轮抢救**（`s04m_wayback.py`，新）：s04j 常规救回 0 后，改走 Wayback Machine ——
> CDX 按域批量查档（688/7354 有存档）→ 限速下载（2s/请求，遭遇 archive.org bot 封锁数小时后解封）
> **成功 687/688**；另 `--mutant` 畸形双协议 URL 内层重建+referer 修复原站救回 **44** 张。
> 落地率 94.91%→**95.42%**，未落地 6619 均为 Wayback 也无存档的真死链。
> 同日**死链封盘**（用户确认放弃剩余 6615 张）：未落地图统一替换为占位图
> （`assets/img-placeholder.png`，s04p 生成、s04d 回写；s06 base_fix 同步支持 assets/ 前缀）。
> 2026-10-29 **死链记录清除**（`s04s_purge_dead.py`，新）：这 6615 张从未落盘的图，其原文地址当时还留在
> parsed / 原始 HTML 缓存 / 子页与列表页缓存 / manifest / 单篇页里，导致每轮增量都被 `s04i` 重新列为待下载
> （实测重试 6754 张、耗时 30 分钟以上）。**第一轮**按 `images[]` 口径清除：改写 5438 个文件、抹掉 89732 处地址。
> **第二轮**又查出 277 个只挂在**评论 `<img>` 属性**上、从未进过 `images[]` 的地址（涉 21 篇）—— 其中 65 个的域仍可达、
> 试抓成功后发现评论图不参与本地化（会变成无人引用的孤儿文件），已原样回滚（图片库复原 143731 个）。
> 两轮合计改写 5501 个文件、清除 **90856** 处，全树复扫残留 **0**。
> 占位图不再回显原址；原始缓存里改写成无 src 的 `<img class="fl-deadimg">`（`extract_images` 认不到 src，
> 重解析也造不回地址）；正文图落地率因此变成 **138210/138210 = 100%**，`s04i` 的无用重试随之归零。
> 分类明细 `_pipeline/lost_images_by_year.txt` 已删除；两轮被改写的原文件整体备份在**项目外**
> `D:\workspace\_dead_purge_backup_20261029\` 与 `D:\workspace\_dead_purge_backup_r2\`（地址清单
> `D:\workspace\_dead_image_urls_6615.txt`、`D:\workspace\_unlanded_img_only.txt`），确认无需回滚后可删。
> 2026-10-25 开通**分页列表页发现通道**（`s01b`）：穷举首页 372 页 + 28 个分类共 482 页归档分页（878 次请求、0 失败），比 sitemap 多发现 **9 篇文章**。
> 同日开通**文章内部分页**（`s02b`）：站点用 nextpage 把长文（如「福利汇总（1024）」）拆成 `/{slug}.html/2../N`，之前只抓了第 1 页。现按页保留、离线站**分页展示**，补回 **1435 篇 / 2284 子页**正文与 **~7.1 万张**此前遗漏的子页图片（并修掉 `s04i` 会把 HTML 错误页当图存为 `.bin` 的老坑）。
> 2026-10-26 **图片抢救**（`s04j`）：历次回补时代理是关的、降级直连，导致一批“只是被墙/防盗链 403”的图被误判死链。代理恢复后走代理 + 域名级 referer，救回 sina/qh **4063** 张 + 虎嗅/京东/腾讯等 **1046** 张，落地率 91.39%→**94.91%**；剩余 7404 为真死链（fuliba2021 系 3885 TLS 失联、fuliba2022 429、其余 404/502、713 条源站写坏的畸形 URL，Wayback 抽查无存档）。

## 2. 目录结构

> 2026-11-04 目录重构：站点外壳与资源留在根，年份目录收进 `data\`，脚本改成 `_scripts\`
> 并按功能分组；旧 `_src\` 拆成 `_pipeline\`（进度台账，删了丢进度）/
> `_rawdata\`（抓回的原始文件）/ `_logs\`（日志与验证快照，随时可删）；
> 图片库从根 `images\` 并入 `assets\images\`。**下划线开头的目录都是工作台，不属于站点。**

```
D:\FULIBA\
├─ index.html                      列表页（搜索/分类/分页），只此一份，预枚举 2011..2050 + undated
├─ archived.html                   归档管理页（待移入/待移出名单 + 已归档名录，s06 生成）
├─ README.md                        根目录唯一的文档；促销删除的评审过程留档已移到 `_pipeline\promo_review.md`
├─ data\                            ★ 年份文章目录（站点发布层）
│   ├─ 2011\ … 2026\                  一年一个自包含目录（迁移 = 复制这一个文件夹），每个只含 4 项
│   │   ├─ data.js                    该年列表元数据，自注册 window.FULIBA_YEAR("<年>", […])
│   │   ├─ _audit.json                该年完整性小清单（条目/单篇页/容器数与首尾日期）
│   │   ├─ article\<url_key>.html      单篇静态页（站点根下三层 → 页内 ../../../assets/images/…）
│   │   └─ material\<url_key>.jsonl    ★ 原料，**一篇一文件**（原始 HTML + 解析产物 + 分页子页；旧名 _src）
│   └─ undated\                       无发布日期文章（理论不应存在）的暂存槽，当前为空
├─ assets\                          站点资源（样式 + 占位图 + 兜底脚本 + 图片库）
│   ├─ style.css                    全站样式（单篇页引用 ../../../assets/style.css）
│   ├─ avatar-placeholder.png / img-placeholder.png / img-fallback.js
│   └─ images\<前2位>\<sha1[:20]>.<ext>
│                                   ★ 图片库（站点里 href = assets/images/…）：增量新图落这里；
│                                     历史 143868 张在另一台电脑上，接回时由 store.py 在
│                                     assets\images 建 junction。台账键名仍是 images/<xx>/<hash>
├─ _scripts\                        脚本清单见 §6（共享层在本目录顶层，步骤脚本按功能分组）
│   ├─ py.cmd                       解释器统一入口（全项目唯一一份；选可用 Python + 强制 UTF-8 + `-u` 转发）
│   ├─ common.py / years.py / store.py   共享层：路径+HTTP+限速+台账 / 年度层 / 图片库位置层
│   ├─ collect\   s01 s01b s02 s02b      发现与抓取（sitemap / 列表分页 / 文章 HTML / 内分页）
│   ├─ parse\     s03                    解析正文/评论/图片清单
│   ├─ images\    s04* 共 12 个          图片层（下载/策略/回写/台账/代理还原/抢救/清洗）
│   ├─ build\     s06 s07 rebuild update 建站 / 旧布局转换 / 一键重建 / 增量更新
│   └─ audit\     _verify archive status 交付自检 / 归档名单执行 / 进度面板
├─ _pipeline\                       过程与台账 —— **删了丢进度**，不要随手清（详见目录内 README.md）
│   ├─ <YYYYMMDD>\                 ★ 一天一份 / 一轮一份的新文件都放这里
│   │   ├─ state_<阶段>.jsonl         各阶段审计线索（state_append 只写当天文件，读端合并全部日期）
│   │   ├─ urls_pages_pending.json    s01b 待补差集（逐轮累积的滚动快照）
│   │   ├─ urls_outofscope.json       超年份范围的新发现登记（同上）
│   │   └─ resume_wayback.json        s04m 抢图续跑档（删了会重下已抢回来的图）
│   ├─ urls.json                    文章 URL + lastmod（sitemap 与分页两通道的并集）【单一事实源】
│   ├─ urls_gone.json               已确认失效（404）或已删除文章 URL，s01/s01b 自动排除【同上】
│   ├─ image_manifest.json          URL → 本地图片映射（全局台账，不按年拆）【同上】
│   ├─ images_store.json            图片库位置配置（mode/external_root/junction，见 §13.2）
│   ├─ categories.json              28 个分类归档页清单
│   ├─ skipped_promo.json           促销清理删除台账（含隔离位置与理由）
│   ├─ promo_review.md              促销删除的评审过程留档（v3→v9 规则演进；2026-11-06 从根目录移入）
│   ├─ archive_ops.json             归档名单（浏览器导出后合入，archive.py 消费）
│   └─ pacer_state.json pacer_sh<N>.json  限速器连续成功/失败计数（跨轮累积 → 改成按天重置就等于取消熔断）

    【永远留在根、不许日期化】以上这些的判据是「全集里有没有这条」：漏读 urls_gone 会把已删文章
    抓回，漏读 manifest 会让 s04i 想重下 13.8 万张，漏读 pacer 会让熔断保护失效。
├─ _rawdata\                        抓回来的原始文件（理论可重抓，代价是网络与原站可用性）
│   ├─ sitemaps\                    sitemap 分片缓存（180 个）
│   └─ pages\<分区>\<页码>.html     分页列表页缓存（s01b；29 个分区，命中即不重复请求）
├─ _logs\                           日志与验证快照 —— **随时可删，删了不丢任何数据**（新日志一律进日期目录）
│   ├─ <YYYYMMDD>\                 ★ 一天一份：本轮写的回报与步骤日志都落在这天的目录里
│   │   ├─ rebuild_<step>.log         rebuild 各步骤子进程输出（rebuild.run_step 写）
│   │   ├─ check_report.txt           计数体检表（rebuild --check）
│   │   ├─ verify_report.txt          交付自检（_verify）
│   │   ├─ update_report.txt          增量更新回报（update.py）
│   │   ├─ _stat.txt                  进度面板（status.py）
│   │   ├─ s01b_pages.log             分页扫描进度（s01b）
│   │   ├─ report.md                  抓取报告（s06 全量重建时生成）
│   │   └─ 历史一次性日志          purge_*.log / gap_*.log / promo_hits.json 等（存证用）
│   ├─ shots\<YYYYMMDD>\          浏览器/渲染验证的截图（日期在目录上，文件名不带日期前缀；写用 C.shot_path）
│   └─ README.md                    本目录职责与清理口径
└─ _backup\                         备份区；大改一律按 `<日期>_<事由>\` 建子目录
    ├─ 20261104_dir_restructure\    本次重构前的 scripts 快照 + 关键台账 + 补丁工具
    └─ update_refresh\<YYYYMMDD>\   update.py --refresh 的可逆点（抓取失败自动移回）
```

> 年度化前（2026-10-31 上午）的旧布局早已不存在：当时每篇 2~4 个文件分存三处，外加根目录全局
> `data.js`（5.6MB）、`articles/`（7231 个单篇页目录）、`articles.json`（350MB 汇总）；
> 由 `s07_migrate_years.py` 整转成年目录 + 一篇一容器（旧目录在结项验收后删除，见 §1）。

## 3. 快速开始

```bat
cd /d D:\FULIBA

_scripts\py.cmd _scripts\build\rebuild.py --check    :: 体检：只看计数与一致性，不写任何文件
_scripts\py.cmd _scripts\build\rebuild.py            :: 重建站点（index/各年 data.js/单篇页/report.md）+ 自检
_scripts\py.cmd _scripts\build\update.py --dry        :: 看原站有多少新文章（默认只比对当年）
_scripts\py.cmd _scripts\build\update.py              :: 增量更新（只当年）
_scripts\py.cmd _scripts\build\update.py --years=2025,2026   :: 指定比对年份；--years=all 为全量比对
_scripts\py.cmd _scripts\collect\s01b_collect_pages.py --stats  :: 只看分页规模（零网络）
```

> 不要直接写 `python _scripts\build\xxx.py`。`_scripts\py.cmd`（唯一入口，项目根不再有转发壳）会自动挑选可用解释器（venv 标准库损坏时回退到
> 同版本完整解释器 + 原 venv 的 site-packages），并统一 UTF-8 输出，避免 GBK 乱码。

## 4. 重建（站点丢失 / 迁移 / 想重新生成）

| 命令 | 做什么 | 参考耗时 |
|---|---|---|
| `_scripts\py.cmd _scripts\build\rebuild.py --check` | 只统计计数并给一致性结论 | 10~20 秒 |
| `_scripts\py.cmd _scripts\build\rebuild.py` | `s06` 建站 + `_verify` 自检 | 5~12 分钟 |
| `_scripts\py.cmd _scripts\build\rebuild.py --parse` | 先 `s03` 补解析缺失分片，再建站 | +2~4 分钟 |
| `_scripts\py.cmd _scripts\build\rebuild.py --all` | `s01→s01b→s02→s02b→s03→s04i→s04l→s04f→s04d→s06→_verify` | 视缺口而定 |

- 全部步骤幂等，**不会删除任何既有数据**；
- 每步日志写 `_logs/<日期>/rebuild_<step>.log`；
- 结束打印计数体检表（同 `_logs/<日期>/check_report.txt`）；
- 耗时受磁盘负载/杀软扫描影响，实测 `s06` 在 96s ~ 418s 之间波动，属正常。

## 5. 增量更新（原站发了新文章）

| 命令 | 说明 |
|---|---|
| `_scripts\py.cmd _scripts\build\update.py --dry` | 只算增量，不抓不写 |
| `_scripts\py.cmd _scripts\build\update.py` | 执行增量更新 |
| `_scripts\py.cmd _scripts\build\update.py --refresh` | 连 lastmod 变化的旧文章一起刷新 |
| `_scripts\py.cmd _scripts\build\update.py --force` | 无新增也重建站点；同时切到**全树档**（不写范围文件，逐篇重收敛） |
| `_scripts\py.cmd _scripts\build\update.py --deep` | s01b 穷举全部分页（首次补漏 / 定期深扫，~850 请求） |
| `_scripts\py.cmd _scripts\build\update.py --pages=5` | 只处理前 5 篇新增（试跑） |
| `_scripts\py.cmd _scripts\build\update.py --years=2025,2026` | **指定比对年份**；省略则只比对当年，`--years=all` 为全量 |

只想做「分页发现」本身（不建站）：

```bat
_scripts\py.cmd _scripts\collect\s01b_collect_pages.py --stats        :: 只看规模，零网络
_scripts\py.cmd _scripts\collect\s01b_collect_pages.py --quick --dry   :: 各归档第 1 页快扫，只报差集
_scripts\py.cmd _scripts\collect\s01b_collect_pages.py                 :: 穷举全部分页并合并进 urls.json
_scripts\py.cmd _scripts\collect\s02b_fetch_pagenav.py --scan          :: 只统计带内部分页的文章与子页数，零网络
_scripts\py.cmd _scripts\collect\s02b_fetch_pagenav.py                 :: 逐页抓文章内部分页子页（幂等）
```

行为与约定：

1. `s01` 重拉 sitemap 合并 `urls.json`，并按 `urls_gone.json` 排除已 404 的文章；
2. **`s01b` 再翻分页列表页**（首页 `/page/N` + 28 个分类 `/xxx/` 与其 `/xxx/page/N`）：
   sitemap 与分页是两套互不包含的发现通道，只跑 `s01` 会漏掉「分页里有、
   sitemap 里没有」的文章；默认 `--quick` 只翻各归档第 1 页（~29 请求 / 40 秒），
   穷举交给 `--deep`；
3. **`s02b` 拓文章内部分页**（`/{slug}.html/2../N`）：站点用 nextpage 把长文拆页，sitemap/列表页
   只给第 1 页。子页存进同一篇年度容器的 sub 段（不合并）；新抓到子页时会删该篇 parsed
   以触发 s03 重解析组装 `pages[]`；离线站据此分页展示；
4. 只处理 `parsed/` 中不存在的 URL；`s02/s03/s04i` 均「已完成则跳过」，天然增量；
5. **已有页面不受影响**：`s06` 用 `url_key` 生成目录名，旧文章路径与内容幂等重建；
6. `--refresh` 先把受影响那几篇的年度容器移入 `_backup/update_refresh/<YYYYMMDD>/` 再重抓，抓取失败自动移回；
7. 结束输出校验表（urls / html / parsed / images / manifest / articles / data.js 及增量），
   写入 `_logs/<日期>/update_report.txt`；
8. **图片库闸门**（§13）：`update.py` 开局打印 `[图片库] state=...`；图片库在本机之外且当前不可达时
   `s04i/s04l/s04f/s04d` 整组跳过（记为跳过、不算失败），站点照常重建，
   新文章的正文图临时按远程地址降级显示，库恢复可达后 `update.py --force` 自动补齐。
   这一步的存在是为了杜绝「库不可达时跑增量 = 本机重下 13.8 万张 + 抹掉 manifest 台账」。
   **当前配置是 `mode=incremental`（state=incremental）**：图片步骤不跳过，但只处理增量——
   只下 `manifest` 里没有的新图到本机 `assets/images/`，历史引用与台账一律不动（§13.2）。
9. **年份范围**（2026-10-31 年度化）：默认只比对当年——`s01` 按 sitemap 月片文件名里的 YYYY 过滤，
   往年月片根本不拉不解析；发布年不在范围内的新发现进 `_pipeline/<日期>/urls_outofscope.json` 备查（不静默丢）。
   跨年夜发现上年补发文章时，用 `--years=2025,2026` 指定两年即可；逃生阀 `--years=all`。
   新文章落哪个年目录由**解析出的发布日**决定（抓回时先落 `undated/`，s03 解析出日期后自动归位），
   所以 `--years` 不会把文章存错年份。
10. **内容未变零写入**：`s04d` 回写容器、`s06` 写单篇页/年度 data.js/index.html/_logs/<日期>/report.md 前都先比对内容，
    相同就不写（mtime 不变）。这是迁移侧的硬要求：robocopy 按 mtime+size 判变化，
    若每轮全重写就会把 7 千个页面当成待传。实测：s06 二次运行 **写盘 0 / 跳过 7,252**。
    ⚠ 比对读取必须 `newline=""`（默认读法会把 CRLF 翻译成 LF，导致内容相同也判为变了而重写）。
11. **列表页缓存按归档分区 + 写前验真**（2026-10-31）：`_rawdata/pages/<分区>/<页码>.html`，分区名就
    是归档前缀（`fuli`、`公告`…，首页 `_home`），旧名是 sha1(url) 不可逆也看不出翻到哪页；
    旧哈希桶由 `s01b --migrate-cache`（默认试算，加 `--apply`）搬迁并清掉重复副本。
    ⚠ 写缓存前必须过 `looks_like_list()`：旧代码不校验响应体，结果旧缓存 4,486 个里有
    **3,467 个是同一页**（canonical 均 `/dytj/page/40`，当年 --deep 后期站点把同一内容
    回给所有后续请求）加上 29 个无 canonical 的「提示信息」顶包页 —— 它们等于“这页已翻过
    但没有新文”，会静默弄断发现通道。清理与改名不影响任何文章数据，只影响下次 --deep 的耗时。

12. **范围模式（2026-11-04）**：以前有 1 篇新增也得把整个库重跑一遍，只为确认
    「另外七千篇不用动」（实测一轮 614 秒里约 480 秒花在这上面，与新增数无关）。
    现在 `update.py` 把本轮 todo 的 URL 写成 `_pipeline/<日期>/update_keys.json`，
    给 `s02/s02b/s03/s04i/s04f/s04d` 递上 `--keys-file=`，`s06` 则按触及年份走 `--years=`；
    一轮 1 篇的实测从 614 降到 **106~158 秒**（其中 s01b 限速 46~49 秒 + 全树自检 34~59 秒，
    真正处理只用了 8 秒）。口径与限制：
    - **不带 `--keys-file` 就是全树**（一字不变的历史行为）：`rebuild.py --all`、
      手工单跑任何步骤、以及 `update.py --force` 都是这一档。范围文件**不会被自动发现**
      （不走 `pipeline_find`），免得拿昨天的范围跑今天的一轮而静默漏文。
    - **`_verify` 永远全树**：它是范围模式的回归门（报「容器缺解析 N」与破图）。
      范围档本身不再自动修「上一轮中断留下的半成品」，所以**定期（建议每月或每次改完
      脚本后）跑一次 `update.py --force` 或 `rebuild.py --all`** 把它们重收敛。
    - **旧文章在站内新增内部分页（第 N+1 页）范围档发现不了**：`s02b` 范围模式下只探
      本轮新增那几篇。要拿住这种变化用 `--refresh`（lastmod 变新就进范围）或全树档。
    - `s06` 只按**年**收敛，不按篇收敛：局部重建的孤儿页清理拿「本轮 loaded 的全部 key」
      当保留集，少给一篇就会把该年其余单篇页删掉（`step_extra` 里已避开这条路）。
      有文章还没归位到具体年份（undated / 抓取失败）时，s06 自动退回全树。
13. **范围是动态的**：`s02b` 如果给「本已在范围内」的文章抓到新子页，会先 `drop_parsed`
    再写回范围（`C.scope_add()`）；将来若有步骤改到了范围外的已有文章，也必须这样
    追加回去，否则 s03/s04d/s06 会漏做它，库里就半新半旧。全树模式下 `scope_add` 不做任何事。

> 校验是**展示性质、非强制**：差异不改变退出码，由使用者决定是否处理。
> 正常差异来源：sitemap 里但站点已 404 的文章、源站死链图（页面保留远程 URL 降级显示）。

## 6. 脚本清单

> 全部在 `_scripts\` 下：共享层（common/years/store）在顶层，步骤脚本按
> `collect\` / `parse\` / `images\` / `build\` / `audit\` 分组；下表只写文件名。
> 调用一律 `_scripts\py.cmd _scripts\<组>\<脚本>.py`（rebuild/update 的流水线表也按这个相对路径找脚本）。

| 脚本 | 职责 |
|---|---|
| `common.py` | 路径 / 限速器 / HTTP 会话（含代理降级直连） / 台账 / JSON 读写 / 内容未变不写 / **本轮范围文件**（`scope_write`/`scope_read`/`scope_add`/`scope_arg`/`scope_keys`）/ 图片键↔磁盘翻译（`site_rel`/`img_disk_path`/`img_key_from_disk`） |
| `years.py` | **年度层单一事实源**：`data/<年>` 路径（`data.js`/`_audit.json`/`article/`/`material/`）与站点访问前缀（`SITE_PREFIX`/`PAGE_BASE`）+ 一篇一容器（JSONL）读写 + meta 行统计 + 年度对账 + `--years` 解析；本模块不 import common（common 要 import 它） |
| `s07_migrate_years.py` | 旧布局 → 年度容器的一次性转换：默认试算，`--apply` 真转（只新建不动旧文件），`--verify` 重校，`--retire` 同卷改名归档旧目录 |
| `s01_collect_urls.py` | sitemap → `urls.json`（与已有清单合并，排除失效 URL） |
| `s01b_collect_pages.py` | 分页列表页发现通道（首页 + 28 分类归档）；缓存按 `<分区>/<页码>.html` 存放，`--migrate-cache` 可把旧哈希桶改名+清垃圾 |
| `s02_fetch_html.py` | 抓文章原始 HTML → 写入年度容器的 raw 段（限速、断点续传、熔断）；`--keys-file=` 只抓本轮范围 |
| `s02b_fetch_pagenav.py` | 逐页抓文章内部分页（nextpage）子页 → 写入同一容器的 sub 段；`--keys-file=` 只探本轮范围，抓到新子页会把该篇**追加回范围**（否则旧文章多出一页时会半新半旧） |
| `s03_parse.py` | 解析正文 / 评论树 / 图片清单 → 写入容器的 parsed 段（分页文章组装 pages[]/page_count）；按发布日归年；`--keys-file=` 只解析本轮范围（范围模式下不开全树 meta 扫） |
| `s04i_fetch_body_fast.py` | 正文图高速下载（manifest 命中即跳过；代理不通自动直连）；`--keys-file=` 只从本轮范围收集待下载 |
| `s04j_rescue.py` | 未落地图片抢救（强制代理 + 域名级 referer + 死域自动拉黑 + 畸形 URL 重建） |
| `s04l_proxy_unwrap.py` | 还原站点图片代理地址后直取 |
| `s04f_rebuild_manifest.py` | 从磁盘重建 manifest（原扩展名优先）；`--keys-file=` 只从本轮范围收 URL（台账清理那一遍仍扫全 manifest，它是全局判定） |
| `s04d_rewrite.py` | 回写 `images[].local` 与 `<img src>`、头像占位（只改容器 parsed 段，touch=False + 内容未变不写）；`--keys-file=` 只回写本轮范围（全树档一轮要 165~193 秒） |
| `s04_fetch_images.py` | 图片采集公共函数（被 `s04f` 复用） |
| `s04b_image_policy.py` | 图片策略常量（被 `s04i` 复用） |
| `s04p_make_avatar_placeholder.py` | 生成默认头像占位图 |
| `s04q2_purge_recycle.py` | 目录级清除 `_backup/_orphans/`、`_backup/_recycle_avatars/` 隔离区 |
| `s04r_fix_broken_refs.py` | 坏本地引用修复（0 字节空壳 / 降级帧） |
| `s04s_purge_dead.py` | 清除「从未落盘」死链图在项目里的全部原文地址记录（默认试运行；`--apply` 要求备份目录在项目外） |
| `build/s06_build_site.py` | 生成 `index.html`（预枚举年份槽、运行时合并）/ `data/<年>/data.js` / `data/<年>/article/<key>.html` / `data/<年>/_audit.json` / `_logs/<日期>/report.md`；支持 `--years=` 局部重建 |
| `store.py` | **图片库位置层**：读 `_pipeline/images_store.json`，判 local/attached/detached/incremental/broken 五态，junction 自愈、闸门 `guard_or_exit`、`new_images_only()`、`--status/--json` 状态查询（详见 §13）。只管「库在哪」与「跑增量时能不能动图片层」，**不含任何搬运逻辑** |
| `archive.py` | 归档名单执行器：`--import` 合并站内导出名单 → `--apply` 移入（删资源+登记）/移出（重抓，走同一套范围口径，失败回滚） |
| `_verify.py` | 交付自检（覆盖 / 评论 / 广告 / 图片 / 结构） |
| `status.py` | 进度查询 → `_logs/<日期>/_stat.txt` |
| `rebuild.py` | 一键重建（四档；计数走年度层 meta 行统计）；**范围工具住这里**（`SCOPE_STEPS`/`step_extra`/`touched_years`，update.py 与 archive.py 共用） |
| `update.py` | 增量更新（s01 + s01b 双发现通道 + `--years` 年份范围 + **范围模式**：写 `update_keys.json` 并给各步骤递 `--keys-file`，`--force` 回到全树档；含校验报告） |
| `_scripts\py.cmd` | 解释器统一入口（**唯一一份**，在 `_scripts\`）。选可用 Python + 强制 UTF-8 + `-u` 转发 + 原样透传退出码（`rebuild.py` 靠 s04i 的 rc=4/5 判图片库闸门跳过，所以不能吞返回码）。项目根那份 3 行转发壳已于 2026-11-06 删除 —— 它存在的唯一理由是让裸 `py.cmd` 在根目录敲得通，代价是往站点根塞工作台文件；命令一律写成 `_scripts\py.cmd _scripts\<组>\<脚本>.py` |

## 7. 数据分层（哪些能删、哪些是原料）

| 路径 | 角色 | 能否删 |
|---|---|---|
| `data/<年>/material/<url_key>.jsonl` | **原料（一篇一文件）**：原始 HTML + 解析产物 + 分页子页；建站唯一权威数据源 | ❌ 不可再生（原站图床大量已死、死链地址已清除） |
| `data/<年>/_audit.json` | 该年完整性小清单（s06 生成，供拷贝后验收） | ⚠ 可重建（跑一次 s06 即有），但删了就没“快速验收”这条路 |
| `data/<年>/data.js` `data/<年>/article/<key>.html` | 交付产物（可由容器重建） | ✅ 可删，`rebuild.py` 重建 |
| `_pipeline/urls.json` | 文章 URL + lastmod | ❌ |
| `_pipeline/urls_gone.json` | 已确认 404 的 URL，`s01`/`s01b` 自动排除 | ❌ 删了 404 文章会被合并回来 |
| `_pipeline/<日期>/urls_outofscope.json` | 往年新发现但未纳入比对的登记（逐轮累积的滚动快照） | ⚠ 删了下次会重新登记，不丢数据 |
| `_rawdata/pages/` | 分页列表页缓存（按归档分区存放） | ⚠ 删了下次全量重翻 ~850 页；现在一眼能看出哪个归档翻到哪一页 |
| `_pipeline/categories.json` | 分类归档清单（每次整体覆盖） | ⚠ 可重建 |
| `_pipeline/<日期>/urls_pages_pending.json` | 分页发现台账（逐轮累积的滚动快照） | ⚠ 可重建，丢了重扫一轮 |
| `_pipeline/image_manifest.json` | URL → 本地图片映射（全局台账，不按年拆） | ❌ |
| `_rawdata/sitemaps/` | sitemap 分片缓存 | ⚠ 删了下次全量重抓 sitemap |
| `index.html` `archived.html` `assets/`（含 `style.css` 与兜底脚本） `_logs/<日期>/report.md` | 交付产物 | ✅ 可删，`rebuild.py` 重建 |
| `assets/images/` | 采集成果（**不可再生**：原站图床大量已死，且死链地址已按 2026-10-29 清除，删了没法重抓） | ❌ 不要删。历史库在另一台电脑上；本机 `assets/images/` 现在只存**增量新图**（§13.2），这些新图同样不可再生 |
| `_logs/`、`_pipeline/<日期>/state_*.jsonl`、`pacer*.json` | 日志 / 审计线索 / 限速状态 | ✅ 可删，不影响正确性 |

## 8. 关键约束（违反会静默出错，务必遵守）

1. **图片命名不变量**：`images/<sha1(url)[:20] 前2位>/<sha1(url)[:20]>.<ext>`。
   写 `assets/images/` 的新脚本**不得**使用 `common.url_key()`（那是 16 位，用于 HTML 分片），
   否则会产生 manifest 与回写都识别不了的重复文件，且不报错。
   1.1 **键只有一种前缀形状**：台账 `image_manifest.json` 与容器 `images[].local` 里永远存
       `images/<xx>/<hash>.ext`；只有页内 href 与磁盘路径才带 `assets/`。两个方向的翻译
       只允许走 `common.img_disk_path()`（键→磁盘）与 `common.img_key_from_disk()`（磁盘→键），
       **不得再写 `os.path.relpath(p, ROOT)`**——2026-11-04 图片库并进 `assets/images/` 后
       就是这个漏子让 279 条新键带了 `assets/` 前缀，打断 s04d 的幂等判定（见 §10）。
2. **图片有效性 = 魔数校验**，不是「文件存在且非空」。
   `FFD8FF`=JPEG、`89504E47`=PNG、`GIF8`=GIF、`RIFF….WEBP`=WEBP，不符即无效。
   历史事故：25 个 HTML 错误页被存成 `.bin` 并回写进页面，自检全绿但页面破图。
3. **步骤顺序**：`s04f` 必须在 `s04d` 之前（`s04d` 会重填 `images[].local`）。
   另：`s04r` 的回写会让 `s04d` 显示「本地化 0 处」，属正常的幂等结果。
4. **GIF 必须存原图**（`/large/`），不得转静态首帧或压缩到 1600px——
   信息不可逆丢失。下载 sinaimg 需 `trust_env=False` 直连 + `Referer: https://weibo.com/`。
5. **删掉一张图要四处同步**：磁盘文件、manifest 条目、`content_html` 的 `<img>`、`images[]` 条目。
   只删文件会让 `s06` 的图集把图「复活」。
6. **sitemap 不会移除已删文章**：必须靠 `urls_gone.json` 排除，否则每次 `s01` 都会把它们合并回来。
6.1 **发现必须双通道**：sitemap（`s01`）与分页列表页（`s01b`）互不包含，只跑一边必有遗漏。
   分页页数上限取页面里的「共 N 页」—— 实测超页（`/page/99999`、`/fuli/page/161`）
   **返回 200 + 末页同内容**，靠状态码判停会永远停不下来；溢出页只请一次用于确认。
6.2 **`s01` 不得覆写 `urls.json`**：必须与已有清单**合并**。曾经直接覆写，
   导致分页通道补进来的文章每跑一次 s01 就被抹一次（靠 s01b 重跑才能恢复）。
6.3 **发现有三层，缺一必漏**：sitemap（`s01`）、列表页分页（`s01b`）、文章内部分页
   （`s02b`，`/{slug}.html/2../N` 的 nextpage）。三层互不包含，只跑前两层仍会漏掉
   分页文章的**第 2 页及以后的正文与图**（实测补回 1435 篇 / 2284 子页 / ~7.1 万张图）。
   子页页数上限取正文分页条（`.article-paging`）里本文 slug 的最大页码；溢出页
   与上页内容相同即停（同 §8 列表页溢出同理，不能靠状态码）。
7. **控制台是 GBK**：脚本统一 `sys.stdout.reconfigure(errors="replace")`，
   日志写 `_logs/<日期>/*.log`（UTF-8）再读；判断结果只看数字与 ASCII 标记。
8. **批量统计用 Python `os.walk`**，不要用 `du -sm` / `find | wc -l`（6.7 万文件会卡死）。
   遍历 7419 个 parsed 分片用 `ThreadPoolExecutor(16)` 约 40 秒，串行要 4 分钟。
9. **Wayback 抢救死图**：CDX 查询加 `filter=statuscode:200` 且只认 `mimetype:image/*`，
   取原件用 `web.archive.org/web/<ts>id_/<原URL>`；必须**串行 + 25 秒起步退避**（并发 4 即大量 429）。
10. **评论头像统一占位图** `assets/avatar-placeholder.png`，不逐个本地化；
    `assets/` 丢失时用 `s04p_make_avatar_placeholder.py` 重新生成。
11. **年度层只动目录布局，不动图片键**：图片库与资源永远在站点根的 `assets/`（图片在 `assets/images/`），
    单篇页固定落在 `data/<年>/article/<url_key>.html`（从站点根算三层）——**层深不得再随手变**，
    变了页内前缀就得重写（2026-11-04 搬进 `data/` 是刻意做的，跑了一遍全量 s06 重生成）。
    页内前缀只允许由 `years.PAGE_BASE` 与 `common.site_rel()` 给出；台账里的图片键始终是
    `images/<xx>/<hash>.ext`，13.8 万条一条没改，所以换图片库位置不牵动 parsed/data.js/单篇页/manifest。
    也不再“一篇一目录”（目录数是 Windows 元数据成本主体）。**图片不按年拆目录**
    （一张图可被多篇/跨年文章引用）。读写年度路径只走 `years.py`（`article_dir/article_page/count_pages`），
    不要在脚本里拼路径。
12. **容器行顺与段标记**：`<年>/material/<key>.jsonl` 每行一个片段，首字段必为 `"t"`
    （meta → parsed → raw → sub×N）；旧转换出的容器 raw 在最后一行、`"t"` 不在首位，
    读取端必须按「解出 `t` 再判断」而不是比对字符串前缀。任何「内容相同就不写盘」的比对，
    两边都必须 `newline=""` 读（默认文本模式会把 CRLF 译成 LF，导致永远不相等而全树重写）。

## 8.1 抓取限流参数（硬性要求，改脚本时不要调低）

| 阶段 | 基础间隔 | 抖动 | 周期性休息 | 说明 |
|---|---|---|---|---|
| `s01` sitemap | 0.8s | 0.5s | 每 30 次休 2s | 分片命中缓存则不请求 |
| `s01b` 分页列表页 | 1.2s | 0.8s | 每 50 次休 3s | `_rawdata/pages/` 命中则不请求；页数上限取「共 N 页」，不靠 404 判停 |
| `s02` 文章 | 1.2s | 0.8s | 每 50 次休 3s | 分片并行时 base 2.0s / 抖动 1.0s，每片独立 pacer 状态文件，站点侧并发 ≤ 2 |
| 失败退避 | — | — | — | 429/403/502/503/504 按 2→4→8→15→30s 退避；**连续 8 次失败即熔断停止** |

- 所有对站点的请求都走 Clash 代理 `127.0.0.1:7890`，`verify=False`（等价 `--ssl-no-revoke`）。
  代理端口不通时 `make_session`（含 `s01b`）自动降级直连并在日志里提示一次。
- 实测全量抓取（7428 篇 + 图片）全程 **0 次 429、0 封禁**，不要为提速调低间隔。
- 图片通道不受此限：`s04i`/`s04l` 屏蔽系统代理环境变量（`trust_env=False`）后按域**显式**
  走 Clash；代理端口不通时自动降级直连（实测 sinaimg 原图直连可 200，不降级会把
  可取的图误归为「未落地」）。瓶颈是带宽（sinaimg 实测约 7 张/秒）。

## 9. 已定性的「缺口」（不是缺陷，不必再追）

| 现象 | 数量 | 定性 |
|---|---|---|
| 评论标注数与实际解析数不符 | 265 篇 / 11051 条 | **站点侧不可取**：252 篇无评论容器（标注数虚高）、13 篇服务端只渲染部分楼层（翻页返回同一批楼层） |
| 正文图从未取到 | 6615 张 | **真死链（数据源边界）**：fuliba 自己的历史图床 `tu/1.fuliba2021.com` 等共 3885 张（域名 TLS 失联，http 502）、`fuliba2022/2025` 429、其余第三方 404/502、数百条源站写坏的畸形 URL（内层多指向已死的 fuliba2021）。代理恢复后经 `s04j`/`s04m` 两轮抢救（+4063 sina/qh、+1046 虎嗅/京东等、+687 Wayback）后剩这批，Wayback 亦无存档。**2026-10-29 `s04s` 已清除其全部原文地址**：正文只留占位图，不再回显原址，`s04i` 也不再重试，故自检口径的「未落地」归零、落地率变 100% —— 数据源边界本身没有变化 |
| 站点自建图片代理 `s.php?u=` | 323 张 | 已全部还原内层真实地址并本地化 |
| sitemap 中 404 文章 | 1 篇 | `home-for-rent.html`，已登记 `urls_gone.json` |

## 10. 历史事故摘要（已修复，供追溯）

| 事故 | 根因 | 现状 |
|---|---|---|
| 959 个 0 字节空壳图 | GIF 转帧中断 + 幂等跳过不校验文件 | 已清零，`s04d` 已加固为「存在且非空才跳过」 |
| 138 张 16 位短名图 | 误用 `common.url_key()` 命名 | 已迁移为 20 位，脚本已加注释警告 |
| 2776 处 GIF 被静态首帧顶替 | 幂等检测按扩展名顺序命中降级帧 | 已重下 3039 个原图，`find_native` 只认 URL 原始扩展名 |
| 25 个 HTML 伪装成 `.bin` | 图床对失效路径返回 200 HTML | 14 张从 Wayback 恢复、2 张重下、9 张移除（见 §8 第 2 条） |
| 图片 manifest 三处一致性 bug | 同哈希多扩展名取 `cands[0]`、指向 0 字节 | 候选改为「非空 + 原扩展名优先」 |
| WorkBuddy Python 标准库被抽空 | 应用更新期间 `vendor\python` 被反复重建 | `_scripts\py.cmd` 与 `rebuild/update` 内置解释器探测，可自动回退 |
| 279 条图片台账键带了 `assets/images/` 前缀（2026-11-04 发现） | 图片库从根 `images/` 并进 `assets/images/` 时，`s04i`/`s04f`/`s04_fetch_images` 四处还在用 `os.path.relpath(p, ROOT)` 造键，新键与 14 万条历史键不同形；而 `s04d` 的幂等判定只认 `src.startswith("images/")`，下一轮回写就会把已落地的本地图当「未落地」→ 写成占位图（实测 8 篇 280 处） | 已修：键只由 `common.img_key_from_disk()` 给出 + `s04d` 读侧对两种前缀归一化兜底；279 条 manifest 与 8 篇容器已归回 `images/` 键（备份在 `_backup\20261104_imgkey_fix\`），重解析重回写后**交付页逐字节未变**；全树档复测 `s04d`：改写 0 / 7240 零写入、占位图 0 |

## 11. 故障排查

| 症状 | 处理 |
|---|---|
| `ModuleNotFoundError: No module named 'encodings'` | 用 `_scripts\py.cmd` 启动；它会自动回退解释器 |
| 页面破图但文件存在 | 抽样验证文件头魔数（见 §8 第 2 条），HTML 错误页会被当成图片 |
| `update.py` 报告计数差异 | 先看 `_logs/<日期>/update_report.txt` 的差异项；404 文章与死链图属正常 |
| 重建后 `data.js` 条目对不上 | 跑 `_scripts\py.cmd _scripts\build\rebuild.py --check` 对比 urls/html/parsed/articles 四项 |
| 站点目录路径变了 | 不会：目录名取 `sha1(url)[:16]`，只要 URL 不变路径就不变 |

## 12. 浏览方式

直接用浏览器打开 `D:\FULIBA\index.html`（无需服务器）。列表页会预枚举 2011..2050 + undated
这些年度槽位，存哪个年就加载哪个 `data\<年>\data.js`，合并后渲染列表。
单篇页在 `D:\FULIBA\data\<年>\article\<url_key>.html`，可单独打开，图片走 `../../../assets/images/`
相对路径（站点根下三层；不再“一篇一目录”，目录数从 7,530 降到 315）。
未挂载图片库时，加载不出来的图会被 `assets/img-fallback.js` 换成占位图（只作用于渲染，不改数据）。

## 13. 图片库外置（搬运由使用者自己完成）

图片库 `assets/images/` 是全站最占空间的一层（14.3 万个文件 / 32.79 GB），其余全部文件合计只有 2.80 GB。
**项目不提供搬运脚本**——把 images 目录移去哪儿、用什么工具（资源管理器剪切、`robocopy`、
第三方便携盘软件）都由使用者决定；项目只负责两件事：搬完之后怎么让网站继续找到图（junction），
以及搬走之后跑增量时怎么保护图片层不被误动（闸门）。

**当前生效的是 `mode=incremental`（§13.6）**：历史库已整库移到另一台电脑，本机不再试图拿到它，
项目只负责把**新增图片**按原命名规则下到本机 `assets/images/`。

### 13.1 机制

```
图片库（位置自选）  X:\...\images\          256 个哈希分片（全站图片唯一存放处，32.79 GB）

本机 D:\workspace\z_WEB-MOVIES-GALLERY\FULIBA\
  ├─ 除 assets\images 外的一切文件
  └─ assets\images  ──junction──> X:\...\images   ← mklink /J，不需要管理员权限
```

搬完后把 `_pipeline\images_store.json` 的 `external_root` 填成 images 现在的绝对路径，
`_scripts\py.cmd _scripts\store.py --status` 会自动把本机 `images` 建成指向它的 junction。

网站里 13.8 万处 `images/...` 相对引用**一条都不改**（改它要重写 parsed / data.js / 7230 个单篇页）。
junction 让相对路径继续有效：**库可达时图全在；库不可达时网站照常打开，只是旧文章图片显示不出来**（已确认可接受）。
经代码审计确认：全部脚本都用 `os.path.exists/getsize/os.walk/glob` 走 `ROOT/images`，
没有任何 `islink/realpath/followlinks` 判断，因此 junction 对所有读写完全透明。

### 13.2 状态与闸门（`_scripts\store.py`）

图片库五种状态，所有图片步骤在执行前先过这道闸门：

| state | 含义 | 行为 |
|---|---|---|
| `local` | images 是本机真实目录且含全部历史图（没搬） | 与历史行为完全一致 |
| `attached` | 已搬到项目外，且当前可达 | 读写直通外置库；新图直接落库 |
| `detached` | 已搬到项目外，当前不可达 | **图片步骤整体跳过**（退出码 4，不算失败） |
| `incremental` | **历史图在别的电脑（本机拿不到）**，本机 images 只存增量新图 | 图片步骤跑，但只处理增量（§13.6） |
| `broken` | 配置为已外置（external），本机却有个有内容的真实 images | 立刻中止（退出码 5），这是「库不可达时跑过增量」的现场 |

配置在 `_pipeline\images_store.json`：
`mode` / `external_root`（图片库现在的绝对路径）/ `junction` /
`detached_policy` / `max_new_images_per_run` / `prune_circuit_breaker`。

```bat
_scripts\py.cmd _scripts\store.py --status      :: 人读：库在哪、多少个、熔断阈值
_scripts\py.cmd _scripts\store.py --json        :: 机读
```

### 13.3 为什么必须拦：库不可达时跑增量的灾难链（已实测）

不加保护时，一次「图片库不在可达位置就跑 update.py」会造成不可逆后果：

1. `common.py` 导入期 `makedirs(images)` → 本机凭空长出**空**的真实 images 目录；
2. `s04i` 判定「一张都没落地」→ 往本机重下约 13.8 万张（几十 GB）；
3. `s04f` 把「本地文件不存在」的条目当失效清理 → **一次写盘删光 URL→本地文件 台账**（`s04f:183`）；
4. `s04d` 拿不到 `local` → 把占位图/降级地址固化进 `content_html`。

四道独立防线（任何一道单独生效就能阻断）：

| 防线 | 位置 | 触发即 |
|---|---|---|
| 导入期不再盲建目录 | `common.py`（external 模式下只自愈 junction，绝不建真实 images；**incremental 是唯一故意建真实目录的模式**） | 灾难链第 1 步不成立 |
| 已下载判定口径 | `s04i`：incremental 下按「manifest 有条目」判定，不看本机文件在不在 | 灾难链第 2 步不成立（根本不重下） |
| 台账保护 | `s04f`：incremental 下「本机文件缺失」计入「在外部库」，不参与清理；`s04d`：历史引用原样保留 | 灾难链第 3/4 步不成立 |
| 抢救脚本拒跑 | `s04j` / `s04r` / `s04m` 在 incremental 下直接 `return 0`（打印 `S04J_SKIP_INCREMENTAL` / `FIXBROKEN_SKIP_INCREMENTAL` / `S04M_SKIP_INCREMENTAL`）；`s04s` 的「已落地」判定改按 manifest | 防止把 13.8 万张当死链重下/清洗 |
| 步骤闸门 | `s04i/s04l/s04f/s04d/s04r/s04j/s04m` 开头 `C.guard_or_exit()` | 退出码 4，`update/rebuild` 记为「跳过」不算失败 |
| 下载熔断 | `s04i` 待下载 > `max_new_images_per_run`（默认 3000） | 退出码 6，`S04I_BLOCKED_MASS_DOWNLOAD` |
| 清理熔断 | `s04f` 拟清理条目 > `prune_circuit_breaker`（默认 50） | 退出码 7 且**不写盘**，`S04F_BLOCKED_PRUNE` |

后两道与盘符/junction 无关，即使有人手工删了 junction 也不会把台账抹掉。
fixture 实测：100 条缺失引用 → `s04f` 退出 7 且 manifest 逐字节未变；10 条 → 正常放行。

### 13.4 搬完之后（搬运本身不在本项目职责内）

把 `images` 目录搬到哪儿、用什么工具搬，由使用者自己决定；搬完只需要做一件事——
把 `_pipeline\images_store.json` 的 `external_root` 填成新位置的绝对路径，然后
`_scripts\py.cmd _scripts\store.py --status`（它会把本机 `images` 建成指向该位置的 junction）。

| 动作 | 库可达（attached） | 库不可达（detached） |
|---|---|---|
| `update.py` / `rebuild.py` | 照常；**新图直接写进图片库**（junction 透明），旧图一张不重下 | 正文抓到哪算哪，站点照建；`s04i/s04l/s04f/s04d` 被闸门跳过，日志里记「跳过」；新文章的图临时按远程地址降级显示 |
| 补图 | `_scripts\py.cmd _scripts\build\update.py --force` → s04i 只补 manifest/库里都缺的那些 | 不需要，等库可达 |

> 明确禁止：图片搬到项目外之后**任何**跑法都不允许把历史图片重新下载回本机。这条由 §13.3 的
> 四道防线保证，其中「下载熔断」与 junction 无关，即使误删 junction 也只会停下来报错，
> 不会几十 GB 灌回本机。

### 13.5 搬回本机

搬运方式自选，项目这边只有两步：先删掉 junction（`rmdir D:\workspace\z_WEB-MOVIES-GALLERY\FULIBA\images`
——只删链接，不动库里的文件），再把 `_pipeline\images_store.json` 的 `mode` 改回 `local`，
然后把图片放回 `assets\images\`，跑 `_scripts\py.cmd _scripts\store.py --status && _scripts\py.cmd _scripts\build\rebuild.py --check` 确认。
`mode=local` 时所有闸门回到历史行为（图片步骤正常执行），配置本身不改动任何数据文件。

### 13.6 增量本机模式（`mode=incremental`，当前生效）

历史整库（143868 张 / 32.79 GB）已在**另一台电脑**上，本机不再指望拿到它。口径：

| 环节 | 本模式下的行为 |
|---|---|
| `s04i` | **只下 `manifest` 里没有的 URL**（即本轮新增文章的图），按原命名规则落 `images/<前2位>/<sha1[:20]>.<ext>` |
| `s04f` | 本机 stat 不到的条目 = 「在外部库」，**不删除、不重指向**（日志里报「保留在外部库的条目 N 条」） |
| `s04d` | 已指向 `images/...` 而文件不在本机的引用**原样保留**，不回退远程地址也不写占位图；内容未变的分片**零写入**（连 mtime 都不动） |
| `s04j` / `s04r` / `s04m` | 直接拒跑（rc=0 + `*_SKIP_INCREMENTAL`）：它们把「本机缺文件」当未落地/坏引用，会重下 13.8 万张 |
| `s04s` | `is_landed()` 改按 manifest 判定，不会把正常历史地址当成「从未落盘的死链」清洗 |
| `s06` | 额外生成 `assets/img-fallback.js`，并在单篇页 head 引入 |
| `_verify` | 图片文件级判定分为「已落地（本机新图）/ 在外部库（未评估）/ 丢失」，历史图不计丢失 |

保证「可以没图，但 HTML 必须正常打开」的机制：`img-fallback.js` 在捕获阶段监听 `error`（也补扫
`naturalWidth===0` 的已加载失败图），把任何加载不出来的 `<img>` 换成同目录下的
`assets/img-placeholder.png`，并把原引用写进 `title`。**只作用于渲染，不改数据**；库接回来无需任何改动就自动恢复真图。
实测（2026-10-31）：旧文章页 38 个 `<img>` 全部渲染、破图图标 0（6 张缺图换为占位图）、无 JS 异常。

以后想把库接回本机（让旧文章又能看到图）：

1. 先把本机 `assets\images\` 里的增量新图**并入外部库**（哈希同名，直接覆盖到库对应的 `xx/` 分片）；
2. 删掉本机 `assets\images` 真实目录（`rmdir D:\workspace\z_WEB-MOVIES-GALLERY\FULIBA\assets\images`）；
3. `_pipeline\images_store.json` 改 `mode=external` 并把 `external_root` 填成库的绝对路径；
4. `_scripts\py.cmd _scripts\store.py --status` → 自动建 junction（state=attached），历史图立刻回来。

## 14. 迁移（年度化后：复制一个年份文件夹）

年度化的目的就是把「迁移」变成文件复制，**目标机零后操作**（不解压、不建站、不改配置）。

| 场景 | 要复制的东西 | 目标机动作 |
|---|---|---|
| 只补当年新增 | `data\2026\` 整个文件夹（含 `article\`、`material\`、`data.js`、`_audit.json`） | 双击 `index.html` 即可看到新年文章（其余年份本体就在机上） |
| 新机从零开始看站 | 根文件（`index.html` `archived.html`）+ `assets/`（`style.css` + 兜底脚本 + 图片库 `assets/images/`）+ `data/` 下需要的各年目录 | 同上，无需任何构建 |
| 新机还要继续跑增量 | 上面全部 + `_pipeline\`（台账）+ `_rawdata\`（发现缓存）+ `_scripts\`（代码） | 同样无需构建；`_scripts\py.cmd _scripts\store.py --status` 看一眼图片库位置即可 |

约定与陷阱：

- **目标机双击就能用是硬约束**：所以产物（各年单篇页与 `data.js`）必须跟着拷，不允许「拷完跑一次 rebuild」这种二次操作。
- **图片不进年目录**：`images/` 全局存在，页内相对引用保持 `../../images/…`（一张图可被跨年文章共用，按年拆会重写 13.8 万处引用）。
- 本机 `images` 如果是指向外部库的 junction，**绝不要直接复制整个项目目录**（工具会跟随 junction 把几十 GB 一起拉走）；
  图单独一次拷/挂载，项目目录用 `robocopy … /XJ`（不跟随链接）。
- 追平手段：`robocopy D:\FULIBA \\目标\FULIBA /MIR /MT:32 /XJ /R:2 /W:5`。因为已做到「内容未变零写入」，
  robocopy 按 mtime+size 判定，一轮增量后实际只传十几个文件；再跑一遍同命令报 `Copied=0` 即相当于逐文件校对。
- 拷贝后验收：`_scripts\py.cmd _scripts\build\rebuild.py --check` 会读各年 `_audit.json` 与磁盘实测对账，
  少任何一个单篇页/容器都会报 `2024/articles 清单=835 实际=834` 这样的行（实测有效）；
  比扫全盘快得多，也不依赖 `_pipeline/` 在不在。
- **目录形态于 2026-10-31 又收了一次**：单篇页从 `<年>/<key>/article.html` 改成 `<年>/article/<key>.html`。
  已拷到别处的旧副本**不要只做增量 robocopy**（结构变了、旧路径文件会被当作孤儿留着），
  把涉及的 `20XX\` 整目录重拷一次即可对齐。
- 迁移集建议排掉：`_logs/`、`_pipeline/<日期>/state_*.jsonl`、`pacer_state.json`、`.vscode/`（不影响新机看站，只是新机上日志从零起）。
