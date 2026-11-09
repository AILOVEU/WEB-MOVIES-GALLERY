# -*- coding: utf-8 -*-
"""共享工具: 画廊目录解析 / HTTP 请求 / data.js 读写

目录约定（画廊根目录 = index.html 所在目录，只放文件夹与 html）:
  不加下划线 = 网站运行必需, 会上传到服务器；下划线开头 = 仅本地维护用, 不会出现在服务器上

  index.html            展示页
  assets/images/        海报   -> data.js 中 p 字段前缀 POSTER_PREFIX
  assets/screenshots/   剧照   -> data.js 中 sh 字段前缀 SHOT_PREFIX
  data/                 data.js（影片数据, 网站唯一数据源）+ remote_urls.js（原站图片 URL 映射,
                        index.html 的「原图模式」用；由 08_build_remote_urls.py 生成）
  _backup/              备份文件，按日期分子目录存放：_backup/YYYY-MM-DD/<原名>.bak
                        （*.pre-update.bak = 跑流水线前的上一版快照）
  _rawdata/             【原站与源数据】pages/ 详情页快照、samples/ 原站页面样本、
                        _raw_posts.json / details.json / magnets.json（重新生成用的源数据）
                        —— 删除后需重抓
  _pipeline/            【过程中文件】cookies*.txt 登录态等流水线运行期文件
                        —— 文件名带日期前缀 YYYY-MM-DD_*, 读取时取最新一份
                        —— 删除会导致需重新登录
  _logs/                【日志】文件名强制带日期前缀 YYYY-MM-DD_*, 如 2026-11-03_crawl_errors.txt
                        子目录 magnets-resp/ 响应快照、shots/ 页面验证截图
                        —— 整目录可随时删除, 无任何数据丢失
  _scripts/             本工具链
"""
import json, os, re, ssl, sys, time, random, glob
import urllib.request

# 脚本位于 <画廊根>/_scripts/, 上级目录即画廊根目录
DEFAULT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

BASE_URL = "https://www.9dy.top"

# 目录名（下划线前缀 = 不上传服务器的本地目录; assets/ 与 data/ 无 prefix, 网站要用）
ASSETS_DIRNAME = "assets"
IMAGES_DIRNAME = "images"          # assets/images   海报
SHOTS_DIRNAME = "screenshots"      # assets/screenshots 剧照
DATA_DIRNAME = "data"
BACKUP_DIRNAME = "_backup"         # 备份根目录（内部按日期分子目录存放）
RAWDATA_DIRNAME = "_rawdata"       # 原站文件（删了要重抓）
PIPELINE_DIRNAME = "_pipeline"     # 流水线过程文件（登录态 Cookie 等）
LOGS_DIRNAME = "_logs"             # 日志（可整体删除, 无数据丢失）

# data.js 中使用的相对路径前缀（相对画廊根目录, 即 index.html 所在目录）
POSTER_PREFIX = "%s/%s" % (ASSETS_DIRNAME, IMAGES_DIRNAME)               # assets/images
SHOT_PREFIX = "%s/%s" % (ASSETS_DIRNAME, SHOTS_DIRNAME)                  # assets/screenshots


def gallery_dir():
    """画廊根目录: --dir=xxx 参数 > 环境变量 9DY_DIR > 脚本上级目录"""
    d = None
    for a in sys.argv[1:]:
        if a.startswith("--dir="):
            d = a.split("=", 1)[1]
    if not d:
        d = os.environ.get("9DY_DIR") or DEFAULT_DIR
    return os.path.abspath(d)


def _sub_dir(gdir, *parts):
    d = os.path.join(gdir, *parts)
    os.makedirs(d, exist_ok=True)
    return d


def assets_dir(gdir):
    """<dir>/assets"""
    return _sub_dir(gdir, ASSETS_DIRNAME)


def posters_dir(gdir):
    """<dir>/assets/images —— 海报文件"""
    return _sub_dir(gdir, ASSETS_DIRNAME, IMAGES_DIRNAME)


def shots_dir(gdir):
    """<dir>/assets/screenshots —— 剧照文件"""
    return _sub_dir(gdir, ASSETS_DIRNAME, SHOTS_DIRNAME)


def data_dir(gdir):
    """<dir>/data —— 只放网站要读的 data.js"""
    return _sub_dir(gdir, DATA_DIRNAME)


def backup_root(gdir):
    """<dir>/_backup —— 备份根目录（内部按日期分子目录）"""
    return _sub_dir(gdir, BACKUP_DIRNAME)


def backup_dir(gdir, day=None):
    """<dir>/_backup/<YYYY-MM-DD> —— 当日备份子目录（不存在则创建）

    约定: 备份前先按当天日期建目录, 备份件放目录内, 保留原名（如 data.js.pre-update.bak）。
    """
    return _sub_dir(gdir, BACKUP_DIRNAME, day or time.strftime("%Y-%m-%d"))


def backup_file(gdir, name, day=None):
    """<dir>/_backup/<今日>/<name>"""
    return os.path.join(backup_dir(gdir, day), name)


def backup_copy(gdir, src, name=None):
    """把文件复制到当日备份目录, 返回目标路径"""
    import shutil
    dst = backup_file(gdir, name or os.path.basename(src))
    shutil.copy2(src, dst)
    return dst


def rawdata_dir(gdir):
    """<dir>/_rawdata —— 原站文件（页面快照与样本）"""
    return _sub_dir(gdir, RAWDATA_DIRNAME)


def pages_dir(gdir):
    """<dir>/_rawdata/pages —— 详情页 HTML 快照（parse_details 的输入）"""
    return _sub_dir(gdir, RAWDATA_DIRNAME, "pages")


def samples_dir(gdir):
    """<dir>/_rawdata/samples —— 原站页面/脚本参考样本"""
    return _sub_dir(gdir, RAWDATA_DIRNAME, "samples")


def meta_file(gdir, name):
    """<dir>/_rawdata/<name> —— 流水线源数据 JSON

    （_raw_posts.json / details.json / magnets.json; 网站不读取, 所以不当部署产物）"""
    return os.path.join(rawdata_dir(gdir), name)


def pipeline_dir(gdir):
    """<dir>/_pipeline —— 流水线过程文件（登录态 Cookie 等）"""
    return _sub_dir(gdir, PIPELINE_DIRNAME)


def pipeline_file(gdir, name, day=None):
    """<dir>/_pipeline/YYYY-MM-DD_<name> —— 过程文件的当日写入路径

    与日志同策略：带日期前缀，保留每一代过程文件（如每次登录一份 Cookie），
    不会下次跑就覆盖上一次、也能从名字看出是什么时候产生的。
    """
    return os.path.join(pipeline_dir(gdir), dated_name(name, day))


def latest_pipeline_file(gdir, name):
    """取 _pipeline/ 下同名过程文件最新的一份（按修改时间）; 无则返回 None

    兼容两种命名：`YYYY-MM-DD_<name>` 与不带前缀的旧文件 `<name>`。
    """
    d = pipeline_dir(gdir)
    cands = glob.glob(os.path.join(d, "20*_" + name))
    legacy = os.path.join(d, name)
    if os.path.exists(legacy):
        cands.append(legacy)
    cands = [p for p in cands if os.path.isfile(p)]
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def read_skip_ids(gdir):
    """非影片帖排除清单 -> set[int]

    两处取并集：
      1) <本工具链目录>/skip_ids.txt —— 工具链级默认（跟脚本走，沙箱/副本也生效）
      2) <dir>/_pipeline/skip_ids.txt —— 当前画廊的追加排除（可选）
    一行一个 id, 支持 # 注释（含行尾注释）; 两处都缺失时返回空集。

    没有这份清单时, _scripts/pipeline/02_new_movies.py 会把源站的教程/说明帖当新片加进 data.js。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    ids = set()
    for path in (os.path.join(here, "skip_ids.txt"),
                 os.path.join(pipeline_dir(gdir), "skip_ids.txt")):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line.isdigit():
                    ids.add(int(line))
    return ids


def logs_dir(gdir):
    """<dir>/_logs —— 日志与验证输出, 可整目录删除"""
    return _sub_dir(gdir, LOGS_DIRNAME)


def log_subdir(gdir, name):
    """<dir>/_logs/<name> —— 日志子目录（响应快照/验证截图等）"""
    return _sub_dir(gdir, LOGS_DIRNAME, name)


def dated_name(name, day=None):
    """给日志类文件名加日期前缀: YYYY-MM-DD_<name>

    已带同格式前缀则原样返回（幂等, 不叠前缀）。
    """
    day = day or time.strftime("%Y-%m-%d")
    base = os.path.basename(name)
    if re.match(r"^\d{4}-\d{2}-\d{2}_", base):
        return base
    return "%s_%s" % (day, base)


def log_file(gdir, name, subdir=None, day=None):
    """<dir>/_logs/[<subdir>/]YYYY-MM-DD_<name> —— 日志文件名强制带日期前缀

    日志目录可整目录删除, 但名字必须能看出是哪天产生的（同名日志会天天覆盖）。
    """
    fname = dated_name(name, day)
    if subdir:
        return os.path.join(log_subdir(gdir, subdir), fname)
    return os.path.join(logs_dir(gdir), fname)


def data_file(gdir, name):
    """<dir>/data/<name>（现仅 data.js; 源数据 JSON 请用 meta_file）"""
    return os.path.join(data_dir(gdir), name)


def datajs_path(gdir):
    """<dir>/data/data.js"""
    return data_file(gdir, "data.js")


# 兼容旧调用名: _src → pipeline → _working → 现为 _pipeline（拆出 _rawdata/ 与 _logs/）
src_dir = working_dir = pipeline_dir


def get(url, headers=None, tries=3, timeout=30, data=None, cookie=None):
    """HTTP 请求 -> (文本, 错误)。自动重试 + 退避。"""
    h = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if headers:
        h.update(headers)
    if cookie:
        h["Cookie"] = cookie
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read().decode("utf-8", errors="replace"), None
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1) + random.random())
    return None, str(last)


def sleep_polite(base=0.8):
    """礼貌延时, 防爬限流"""
    time.sleep(base + random.random() * 0.4)


def arg_val(prefix, default=None):
    """取 --key=value 形式的参数值"""
    for a in sys.argv[1:]:
        if a.startswith(prefix):
            return a.split("=", 1)[1]
    return default


def arg_int(prefix, default=None, name=None):
    """取 --key=N 形式的整数参数（统一空值/非数字/负数的报错行为）"""
    v = arg_val(prefix)
    if v is None or v.strip() == "":
        return default
    try:
        n = int(v)
    except ValueError:
        raise SystemExit("参数 %s 需要整数, 实际收到: %r" % (name or prefix, v))
    if n < 0:
        raise SystemExit("参数 %s 不能为负: %d" % (name or prefix, n))
    return n


def has_flag(flag):
    return flag in sys.argv[1:]


def load_datajs(path):
    """解析 data.js -> list[dict]"""
    with open(path, encoding="utf-8") as f:
        js = f.read()
    m = re.search(r"const DATA = (\[[\s\S]*\]);", js)
    if not m:
        raise SystemExit("data.js 中未找到 const DATA = [...]")
    arr = re.sub(r",\s*\]\s*$", "]", m.group(1).rstrip())
    return json.loads(arr)


DATAJS_HEADER = """// 久电影 · 老舅收藏 — 影片数据配置文件
// 由根目录 index.html 读取展示；每行一条影片记录，可直接编辑增删。
// 基础字段: i=id  t=片名  y=年份  r=地区  g=类型  s=体积  v=视频格式（多为空）
//           db=豆瓣评分  a=主演  dr=导演  du=片长  lb=语言  b=字幕  dt=收录日期
//           u=原站链接  p=本地海报路径 assets/images/{id}.jpg（空字符串表示无封面）
// 扩展字段: wr=编剧  aka=又名  syn=简介  sh=本地剧照数组 assets/screenshots/...
//           mg=磁力链接（数组；""=已扫描确认无磁力；字段缺失=未扫描）
//           mgv=磁力获取方式 (click2=二次点击获取)
const DATA = ["""


def atomic_write(path, text):
    """原子写入: 先写 .tmp 再同盘 rename, 避免中断留下截断文件

    （data.js 与三个源数据 JSON 均全量覆写, 非原子写一旦被打断会直接损坏主数据）"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return os.path.getsize(path)


def atomic_write_json(path, obj, indent=None):
    """JSON 版原子写（ensure_ascii=False，与原行为一致）"""
    return atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=indent))


def save_datajs(path, records):
    lines = [DATAJS_HEADER]
    for it in records:
        lines.append("  " + json.dumps(it, ensure_ascii=False) + ",")
    lines.append("];")
    return atomic_write(path, "\n".join(lines) + "\n")


def parse_ids_arg():
    """--ids=1,2,3 或 --ids-file=path.txt -> list[int] 或 None"""
    v = arg_val("--ids=")
    if v:
        return [int(x) for x in re.split(r"[,\s]+", v.strip()) if x.strip().isdigit()]
    f = arg_val("--ids-file=")
    if f:
        with open(f, encoding="utf-8") as fh:
            return [int(x) for x in re.split(r"[,\s]+", fh.read()) if x.strip().isdigit()]
    return None
