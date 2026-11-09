# `_scripts/` — 工具链

全部命令都通过**本目录的** `py.cmd` 调用（唯一入口；2026-11-06 已删掉项目根那个 3 行**转发壳**，
站点根只留站点外壳文件。cmd.exe 只在当前目录找同名命令，所以从根目录必须带 `_scripts\` 前缀）。
它负责选可用解释器 + 强制 UTF-8 + `-u` 实时输出 + 原样透传退出码
（`rebuild.py` 靠 s04i 的 rc=4/5 判「图片库闸门跳过」，所以不能吞掉返回码）：

```
_scripts\py.cmd _scripts\build\rebuild.py --check      体检（不写任何文件）
_scripts\py.cmd _scripts\build\rebuild.py              重建站点（s06 + _verify）
_scripts\py.cmd _scripts\build\update.py --dry         看原站有多少新文章
_scripts\py.cmd _scripts\audit\status.py               进度面板
```

## 布局

| 位置 | 内容 |
|---|---|
| 本目录顶层 | **入口与共享层**：`py.cmd`（解释器入口，全项目唯一一份）、`common.py`（路径/HTTP/限速/台账/落盘）、`years.py`（年度层单一事实源）、`store.py`（图片库位置层） |
| `collect/` | 发现与抓取：`s01_collect_urls.py`（sitemap）、`s01b_collect_pages.py`（列表分页）、`s02_fetch_html.py`（文章 HTML）、`s02b_fetch_pagenav.py`（文章内部分页） |
| `parse/` | `s03_parse.py`：正文/评论树/图片清单 → 容器 parsed 段，按发布日归年 |
| `images/` | 图片层 12 个：`s04i`（高速下载）、`s04_fetch_images`/`s04b_image_policy`（公共函数/策略）、`s04l`（代理地址还原）、`s04f`（重建 manifest）、`s04d`（回写容器）、`s04j`/`s04m`（抢救/Wayback）、`s04r`/`s04s`/`s04q2`（修复/清洗/清理）、`s04p`（生成头像占位图） |
| `build/` | `s06_build_site.py`（建站）、`s07_migrate_years.py`（旧布局转换）、`rebuild.py`（一键重建/体检 + **范围工具 `step_extra`/`touched_years`**）、`update.py`（增量更新） |
| `audit/` | `_verify.py`（交付自检）、`archive.py`（归档名单执行）、`status.py`（进度面板） |

## 五条硬约定

1. **路径只在共享层拼**。年度路径（`data/<年>/…`）只走 `years.py`；站点访问前缀只走
   `years.SITE_PREFIX` / `years.PAGE_BASE`；图片键 ↔ 磁盘/站点路径只走
   `common.site_rel()` / `common.img_disk_path()`，**磁盘→键只走 `common.img_key_from_disk()`**。
   别在步骤脚本里手写 `../../` 或 `assets/images/`，也不准用 `os.path.relpath(p, ROOT)` 造图片键
   （图片库已在 `assets/images/` 下，relpath 造出的键与 14 万条历史键不同形，会打断 `s04d` 幂等）。
2. **步骤脚本必须把 `_scripts/` 挂上 sys.path**（共享层在上一层）：
   ```python
   sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # _scripts/
   import common as C
   ```
   `years.py` / `store.py` 都**不得** import common（common 要 import 它们）。
3. **被流水线调起的脚本一律登记在 `rebuild.py` 的步骤表里**，路径写相对 `_scripts/` 的
   （如 `images/s04i_fetch_body_fast.py`）；`update.py`/`archive.py` 复用 `rebuild.run_step()`。
   新步骤没登记，就等于没进流水线。
4. **增量轮只处理本轮范围**（2026-11-04）。`update.py` 把本轮 todo 写成
   `_pipeline/<日期>/update_keys.json`，参数由 `rebuild.step_extra()` 统一给出：
   在 `SCOPE_STEPS` 里登记过的步骤会收到 `--keys-file=<路径>`，用
   `C.scope_read(C.scope_arg())` 接住，然后给 `years.iter_parsed(keys=…)` 做**读盘前**过滤
   （不是“读完再丢”）。新步骤要支持范围：登记进 `SCOPE_STEPS` + 接 `--keys-file`；
   改到了范围外的旧文章还要 `C.scope_add(path, url)` 追加回去（参考 s02b）。
   三条红线：**不传参数就是全树**（别把范围做成自动发现）、`s06` 只能按年收敛
   （`clean_orphan_pages` 拿本轮 loaded 集当保留集，按篇收敛会误删）、`_verify` 永远全树（回归门）。
5. **过程文件的落点分两类，不许混**（详见 `_pipeline/README.md`）：
   - 一天一份 / 一轮一份的新产物 → `C.pipeline_day_dir()` 或 `C.pipeline_rolling_path(name)`
     （落 `_pipeline/<YYYYMMDD>/`）；读取端配套用 `C.pipeline_find(name)`（会回退旧布局的扁平文件）。
   - 持续读写的**单一事实源**（`urls.json`、`image_manifest.json`、`urls_gone.json`、
     `skipped_promo.json`、`images_store.json`、`categories.json`、`archive_ops.json`、`pacer*`）
     → 留在 `C.PIPELINE_DIR` 根。**日期化它们会打断「全集里有没有这条」的判定**（历史图被重下、已删文章被抓回）。
   - 台账追加走 `C.state_append(stage, …)`、合并读走 `C.state_load(stage)`，不要在脚本里自己拼 `state_*.jsonl`。

写盘口径沿用：`common.write_text_if_changed()` / `s06.write_if_changed()` /
`years._write_parts()` 都是「内容未变不写盘」（比对必须 `newline=""` 且抹平生成时间戳），
这样一轮增量只会把真正改过的文件算进待传集合。
