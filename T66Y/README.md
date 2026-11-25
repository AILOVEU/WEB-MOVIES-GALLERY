# T66Y 离线清单（草榴國產原創區 fid=25）

把 t66y 版面列表爬下来，做成一个可以按标签快速筛选的本地清单页。
**只抓列表，不抓帖子内容** —— 点标题直接跳回 t66y 原页面。

## 现在能干什么

打开 `index.html`（双击即可，不需要服务器）：

- 61 个标签，点一下就能筛（多选是**交集**：選「4K」+「熟女」= 只要同时满足的）
- 关键词搜索标题/作者，全库范围
- 五种排序：页码 / 发帖时间 / 最后回复 / 回复数 / 下载数
- 筛选条件和排序偏好存在浏览器里，下次打开还在
- 点标题 → 新标签打开 t66y 原帖

## 当前数据量

| 项 | 值 |
|---|---|
| 已抓页数 | 100 / 4957 |
| 帖子数 | 9,912 |
| 标签数 | 61（含 4 个结构化标签：置頂/鎖帖/多頁/新回復） |

**只抓到了 100 页，因为游客有硬上限。** 详见下面「登录墙」。

## 登录墙（重要）

2026-11-24 实测：**游客只能翻到第 100 页**，第 101 页起返回「您沒有登錄或者您沒有權限」。
全库 4957 页，要抓全必须带登录态。

### 怎么补上登录 cookie

1. 浏览器登录 t66y.com
2. F12 → Network → 刷新页面 → 点任一请求 → 复制完整 `Cookie:` 请求头的值
3. 存成一行写进 `_pipeline/cookie.txt`（可以带 `#` 注释行）
4. 跑 `python _scripts/inc/crawl.py` —— 会从第 101 页继续，已抓的自动跳过

cookie 过期后脚本会连续撞 3 次登录墙然后停下，不会空转。

## 目录

```
T66Y/
├─ index.html            清单页（唯一入口）
├─ assets/app.js         前端逻辑
├─ data/
│   ├─ index.js          元信息 + 每页标签位图（几十 KB，首屏加载）
│   ├─ shard_XXX.js      每片 50 页的完整记录（约 2 MB，按需注入）
│   └─ titles_XXX.js     每片标题索引（约 300 KB，搜索时才加载）
├─ _rawdata/             原始 HTML（不可再生，删了要重爬）
├─ _pipeline/
│   ├─ cookie.txt        登录 cookie（可选，没有就只抓游客范围）
│   ├─ crawl_done.txt    已抓页码断点（仅全量模式使用）
│   └─ records.json      ★ 累积库：按 tid 唯一的全部记录 + 水位线（删了要重爬）
├─ _scripts/
│   ├─ common/paths.py   目录布局单一来源
│   ├─ common/t66y.py    列表页解析 + 打标签
│   ├─ common/store.py   累积库读写 + 排序 + 虚拟页码分配
│   ├─ inc/crawl.py      抓取（增量：水位线停止 / 全量：翻到底）
│   ├─ inc/merge.py      按 tid 合并进累积库 + 推进水位线
│   ├─ site/build.py     重排页码 + 组装 data/
│   └─ config/tags.json  标签规则（人工可改）
├─ _logs/YYYY-MM-DD/     日志（随便删）
└─ _bak/YYYY-MM-DD/      data/ 覆盖前的快照
```

## 常用命令

```powershell
cd D:\workspace\z_WEB-MOVIES-GALLERY\T66Y

# 增量更新（推荐）：翻到水位线即停，然后合并 + 重组
python _scripts/inc/crawl.py          # 从 p1 往下翻，遇到上次最新那条就停
python _scripts/inc/merge.py          # 按 tid 合并进累积库 + 推进水位线
python _scripts/site/build.py 25      # 重排页码 + 组装 data/

# 全量抓（游客上限内）
python _scripts/inc/crawl.py --full

# 全量重抓 + 刷新（定期深扫 / 怀疑漏帖）
python _scripts/inc/crawl.py --full --force
python _scripts/inc/merge.py
python _scripts/site/build.py 25

# 只抓前 200 页 / 指定区间
python _scripts/inc/crawl.py --pages 200
python _scripts/inc/crawl.py --start 101 --end 300

# 只看合并会新增/更新多少，不写盘
python _scripts/inc/merge.py --dry

# 从 _rawdata 全量重建累积库（库坏了用）
python _scripts/inc/merge.py --rebuild

# 重新组装（改完 tags.json 后跑这个，不用重抓）
python _scripts/site/build.py 25

# 本地预览
python -m http.server 8765
```

## 增量是怎么工作的

列表按**发帖时间降序**（置顶除外），新帖插最前、旧帖整体后移。所以：

- `crawl.py` 记住上次抓到的最新一条（**水位线**，存在 `_pipeline/records.json`），
  下一轮从 p1 往下翻，**遇到它就停** —— 之后的内容上轮已全有。
- `merge.py` 按 **tid** 合并：新 tid 插入，**旧 tid 原样保留不覆盖**（2026-11-25 起不再刷新
  回复/下载计数），**旧记录一条不删**。重抓同一页是幂等的，库只会增长。
- `build.py` 从累积库读，按「置顶在前 + 发帖时间降序」重排，**重新分配虚拟页码**（每页 99 条）。

**为什么不能只重抓前 N 页就完事**：新帖插入后旧页内容整体后移，按页覆盖会让旧页尾部
被新页开头覆盖，直接丢帖。按 tid 累积才安全 —— 物理页码只用于抓取定位，
交付层页码是 build 时重排的（前端无感，仍是 1..N 连续、第 1 页最新）。

## 改标签

编辑 `_scripts/config/tags.json`，加一条：

```json
{ "key": "my-tag", "label": "我的标签", "color": "#ff8800", "words": ["关键词1", "关键词2"] }
```

然后 `python _scripts/site/build.py 25`。**不用重抓**，标签是在 build 阶段算的。

匹配规则：标题包含任一 `words` 就算命中（大小写不敏感）。`key` 改了会让已存的筛选偏好失效，`label` 随便改。

## 为什么数据分三层

全库 4957 页 ≈ 49 万条，每条约 380 字节 ≈ 185 MB。一次性加载浏览器扛不住，所以：

1. **标签位图**（`index.js`，几十 KB）：每页命中了哪些标签，用这个先算出候选页
2. **记录分片**（`shard_XXX.js`，约 2 MB/片）：只加载候选页所在的分片
3. **标题索引**（`titles_XXX.js`，约 300 KB/片）：只在输入关键词时逐片加载

选一个标签时，前端先查位图知道「哪些页可能有」，只拉那几片。全库时选一个稀有标签可能只加载 1-2 片。

## 已知边界

- 游客上限 100 页，要全库必须配 cookie
- 列表页不含分类字段（该版无 typeid 筛选），分类全靠标题关键词
- 标签是关键词匹配，会有误判（比如「母」会命中「母亲节」这类无关词）—— 改 `tags.json` 调
- 置顶帖会出现在每一页，build 时按 tid 去重保留页码最小的那条
- 不抓帖子详情，所以没有封面图、正文摘要、网盘链接识别
