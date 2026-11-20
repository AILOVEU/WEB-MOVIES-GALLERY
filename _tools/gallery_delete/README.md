# gallery_delete —— 画廊已删除帖子的资源清理工具

AIO App 的「画廊」里，FULIBA / JAVBUS 的帖子可在详情页右上角三点菜单标记删除。
标记只写 App 本地集合 + 推一条 op 到 Supabase；**真正的资源与文章清理由本脚本执行**。

## 文件位置

```
<仓库根>/gallery_delete.sh              启动器（Linux/macOS 用；Windows 直接用 python）
<仓库根>/_tools/gallery_delete/
    gallery_delete.py                   全部逻辑，单文件，零第三方依赖
    README.md                           本文件
```

sh 放仓库根是为了好找好敲；实际逻辑在 `_tools/gallery_delete/`，sh 只是转发。
**没有任何绝对路径**：仓库根由 py 自动探测（从脚本目录往上找同时含 FULIBA 与 JAVBUS 的那层），
所以从任何目录执行都能用。

## 用法

在任意目录执行都可以，路径全部相对脚本自身推导：

```sh
python _tools/gallery_delete/gallery_delete.py status            # 看两边清单条数
python _tools/gallery_delete/gallery_delete.py fetch             # 1. 拉 Supabase 删除名单
python _tools/gallery_delete/gallery_delete.py merge             # 2. 合并进本地全量清单（只增不减）
python _tools/gallery_delete/gallery_delete.py apply --dry-run   # 3. 先看会删什么
python _tools/gallery_delete/gallery_delete.py apply             # 4. 真删
python _tools/gallery_delete/gallery_delete.py all --dry-run     # 全流程干跑
```

Linux/macOS 用仓库根的 `./gallery_delete.sh <子命令>`，等价。

| 参数 | 作用 |
|---|---|
| `--dry-run` | 只打印，不写文件不删资源 |
| `--site FULIBA\|JAVBUS` | apply 只处理一个站点 |
| `--root <路径>` | 覆盖仓库根（默认自动探测） |

## 清单文件：权威的一份 + 一个临时中转

**`<site>/data/gallery_deleted.json` 是唯一的删除清单。**

| 文件 | 位置 | 作用 |
|---|---|---|
| `gallery_deleted.json` | `<site>/data/` | **本地权威全量已删除清单，只增不减**。抓取过滤与删除都以它为准 |
| `gallery_resources.json` | `<site>/data/` | **文章 id -> 该 id 名下资源**（`assets/images/...` 路径） |
| `gallery_deleted_remote.json` | `<site>/_pipeline/` | **临时中转**，fetch 写入、merge 后立刻删除 |

`data/` 是交付层，随项目迁移；`_pipeline/` 是过程区（gitignore、可随时清空），
所以中转文件放 `_pipeline`，不会跟着项目走。

**merge 完就把中转删掉。** 留着会出现两份「删除清单」，早晚有人改错那一份。
`status` 里如果看到「待合并中转 N 条」，说明 fetch 后还没跑 merge。

## 文章和资源是两件事

这是本工具的核心原则。

- **文章**：按 id 删容器、分片锚点、`data.js` 列表项，并把 url 登记进 `urls_gone.json`
  防二次抓取。删除记录只记文章，**不记图片地址** —— 同一张图可能被新文章复用，
  记进黑名单会让新文章缺图。
- **资源**：删不删**独立校验**，只看「还有没有别的保留文章引用它」，
  跟它原来属于哪篇文章、那篇文章删没删没有直接关系。

所以 `gallery_resources.json` 是必需的：它让资源校验不必每次扫全部容器
（FULIBA 6800+ 篇、JAVBUS 750+ 篇），也让「这张图归谁」有据可查。

资源位置只认 `<site>/assets/images/`。脚本在同样的目录结构里执行，
不需要感知外置库 / junction。

## 两个站点：同一套语义，各自的结构

**统一由本脚本删除，不再经过 FULIBA 的 `archive.py` 归档模式。**
两个站点都是「清单是权威，脚本直接删」，只是要删的东西结构不同。

### FULIBA

| 删什么 | 位置 |
|---|---|
| 年度容器（raw HTML + parsed + 子页） | `_pipeline/material/<年>/<key>.jsonl` |
| 该篇独占的图片 | `assets/images/<xx>/<hash>.jpg`（与保留文章共用的**不删**） |
| 分片里的整段 | `data/<年>/part<i>.html` 的 `<div class="shard-post" id="<key>">…</div>` |
| 列表项 | `data/<年>/data.js` 的 `FULIBA_YEAR` 数组 |
| 抓取登记 | `_pipeline/urls.json` 移除该 url |
| 防抓回 | `_pipeline/urls_gone.json` 登记该 url |
| 图片台账 | `_pipeline/image_manifest.json` 清对应条目 |

FULIBA 的 id 是原文 URL 的 SHA1 前 16 位，脚本用 `urls.json` 建索引反查 url。

**共用图不删**：删之前先扫全部保留文章的 `images` / `image_local_map` / `pages[].images`，
仍被引用的图跳过。同一张图被多篇文章引用是常态，一刀切会删掉别人的图。

### JAVBUS

| 删什么 | 位置 |
|---|---|
| 原始帖子页 | `_rawdata/posts/<tid>_p*.html` |
| 图片目录 | `assets/images/<tid>/` |
| 详情分片 | `data/posts/shard_<i>.js` 摘掉 `window.THREADS["<tid>"]` 那一行 |
| 列表项 | `data/data.js` 的 `FORUM_INDEX` |
| 图片台账 | `_pipeline/imgmap.json` 清对应映射 |
| 图源倒排 | `data/imgsrc.js` 由 imgmap 重建 |

分片编号**不重排**：前端按 `tid % SHARD_N` 定位，改编号会让所有帖子错位。

## 下次抓取不再获取

**FULIBA**：`urls_gone.json` 是既有机制，`s01`/`s01b` 会把其中的 url 从 `urls.json` 剔除。
本脚本删除时自动登记，所以下一轮 s01 不会再发现它。

**JAVBUS**：`crawl.py` 的 `crawl_threads()` 已接上：

```python
done = thread_done_set()
deleted = thread_deleted_set()
todo = [t for t in tids if t not in done and t not in deleted]
```

`crawl_done.txt` 与 `gallery_deleted.json` 分开存是有意的：
断点是「抓过了」，删除是「不要了」，两者语义不同。

## 凭据（代码里不存任何 key）

**这是公开仓库，代码里没有硬编码密钥。** 只从两处读：

1. 环境变量 `GALLERY_SUPABASE_URL` / `GALLERY_SUPABASE_KEY`
2. 同目录 `config.local.json`（已 gitignore）：
   ```json
   {"supabaseUrl": "https://<项目>.supabase.co", "anonKey": "<你的 publishable key>"}
   ```

两者都缺时 `fetch` 直接报错退出，本地清单不动。

**service_role key 绝不要放这里**，本脚本也不需要它 —— 读 `sync_ops` 用 publishable key 就够。

## 删除记录只记文章，不记图片

这是本工具的一条硬规矩。

**只记文章**：`gallery_deleted.json` 里只有文章标识（FULIBA 的 16 位 key / JAVBUS 的 tid）
和文章 url。`urls_gone.json` 登记的也是文章 url。目的只有一个：让这篇文章不再被二次抓取和解析。

**不记图片地址**：图片依附于文章，文章没了图也就没了，没必要单独记一笔。
更重要的是，同一张图可能被别的新文章复用 —— 如果把图片地址也记进黑名单，
那张图以后就再也下不下来了，新文章会缺图。

所以：
- 图片文件按「是否还被保留文章引用」决定删不删，删了也不留任何图片级的删除记录；
- `image_manifest.json` **只清真正删掉的图**的条目。共用图虽然不再被这篇引用，
  但别的文章还在用，台账条目必须留着，否则那篇的图会失去映射。

## 安全措施

- **所有被删的文件先备份**到 `<site>/_backup/<日期>/gallery_delete/`，包括：
  年度容器、图片文件、原始 HTML、图片目录、被改写的分片/列表/台账。
- `--dry-run` 只打印不动手。
- 删除失败只警告不中断，其余条目继续处理。
- 先全量校验再动文件，不会出现「删了图但文章还在」的半截状态。

## 误删恢复

备份在 `<site>/_backup/<日期>/gallery_delete/`，按原相对路径还原即可。

若备份也被清掉，只能重抓：

- **FULIBA**：`s02_fetch_html.py --keys-file=<scope.json>` → `s03_parse.py` → `s06_build_site.py`
- **JAVBUS**：`crawl.py threads --only=<tid>` → `images.py images --only=<tid>` → `site/build.py`

注意：重抓拿的是站点**当前**内容，浏览数、评论数会变，与删除前不完全一致。

## 已知限制

- 两个站点当前都没有本地视频资源（视频是外链播放器 iframe），所以没有视频删除逻辑。
- FULIBA 的图片库已外迁到移动硬盘，本机 `assets/images/` 下只有一部分；
  台账里有、磁盘上没有的图会被跳过（不是漏删，是本来就不在本机）。