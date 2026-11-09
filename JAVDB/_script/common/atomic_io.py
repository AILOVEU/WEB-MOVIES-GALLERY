# -*- coding: utf-8 -*-
"""断点/大文件的抗撕裂写入与读取。

为什么需要它：本机沙盒会把 `tmp + os.replace`（rename）当危险操作**直接中断进程**（RESUME 第 5 节坑 1），
所以项目里的脚本都是「直接覆写目标文件」。但直接覆写有个对称的风险：**写到一半被杀，文件就撕裂了**
（2026-11-01 暂停 collection_spider 时就撞上了：`collection_ckpt.json` 从 11.2MB 被截成 2.3MB，JSON 解析失败）。

折中方案（不用 rename，也能保证任一刻都有完整副本可读）：
  写：内容先写 `X.a` / `X.b` 两个槽位之一（交替），写完**立刻读回来 json 自检**，通过后再 copyfile 到主文件 X。
  读：依次试 `X`、`X.a`、`X.b`，取第一个能解析的。
这样即使正写到一半被杀：槽位里有一份坏的，但**另一份槽位是上一次完整内容**，主文件坏了也能回退。
"""
# __JDB_BOOT__ 统一 ROOT/sys.path 引导：脚本可从 _script 任意子目录运行、跨目录彼此 import
import os as _os, sys as _sys
ROOT = _os.path.dirname(_os.path.abspath(__file__))
while not _os.path.isdir(_os.path.join(ROOT, "data")) and ROOT != _os.path.dirname(ROOT):
    ROOT = _os.path.dirname(ROOT)
SR = _os.path.join(ROOT, "_script")
for _d in [SR] + [_os.path.join(SR, _x) for _x in _os.listdir(SR)
               if _os.path.isdir(_os.path.join(SR, _x)) and _x not in ("legacy", "__pycache__") and not _x.startswith(".")]:
    if _d not in _sys.path:
        _sys.path.insert(0, _d)
# __JDB_BOOT_END__
import io
import json
import os
import shutil
import time


def log_path(name, logs_dir=None):
    """统一日志路径：强制 `_logs/<YYYY-MM-DD>_<name>`（自动建目录）。
    所有新写的日志都应走这里，保证文件名带日期前缀、便于归档与清理。
    logs_dir 缺省为脚本同级 `../_logs`（遵循 SR=dirname(__file__) / ROOT=dirname(SR) 约定）。
    """
    if logs_dir is None:
        logs_dir = os.path.join(ROOT, "_logs")
    os.makedirs(logs_dir, exist_ok=True)
    return os.path.join(logs_dir, "%s_%s" % (time.strftime("%Y-%m-%d"), name))


def backup_path(name, root=None):
    """备份目标路径：强制 `_backup/<YYYY-MM-DD>/<name>`（自动建当日日期子目录）。
    今后所有“改动前快照”都应走这里，便于按天归档/清理。root 缺省为脚本同级仓库根。"""
    if root is None:
        root = ROOT
    d = os.path.join(root, "_backup", time.strftime("%Y-%m-%d"))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def pipeline_dir(root=None):
    """本次运行的过程目录：`_pipeline/<YYYY-MM-DD>/`（自动建）。"""
    if root is None:
        root = ROOT
    d = os.path.join(root, "_pipeline", time.strftime("%Y-%m-%d"))
    os.makedirs(d, exist_ok=True)
    return d


def record_path(name, root=None):
    """“过程文件”归档路径：强制 `_pipeline/<YYYY-MM-DD>/<name>`。
    只给“每次跑新产生”的导出/快照/临时记录用（如待办名单、运行快照）；
    **单一当前态的 resume 文件（ckpt/cookie）仍固定名留 `_pipeline/` 根，不走这里**，否则会丢进度。
    """
    return os.path.join(pipeline_dir(root), name)


def _newest_slot(path):
    a, b = path + ".a", path + ".b"
    ta = os.path.getmtime(a) if os.path.exists(a) else -1
    tb = os.path.getmtime(b) if os.path.exists(b) else -1
    return b if ta >= tb else a        # 交替：上一次写的是哪个，这次就写另一个


def write_verified(path, text, verify=True):
    """text 落盘：槽位 -> 自检 -> 复制主文件。返回实际写入的槽位。"""
    slot = _newest_slot(path)
    with io.open(slot, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    if verify:
        json.loads(io.open(slot, encoding="utf-8").read())   # 解析不了就抛异常，主文件保持上一版
    try:
        shutil.copyfile(slot, path)
    except OSError:
        pass
    return slot


def read_any(path, decoder=json.loads):
    """依次试主文件与两个槽位，返回 (对象, 实际用的路径)；全坏则 (None, None)。"""
    for p in (path, path + ".a", path + ".b"):
        try:
            if not os.path.exists(p):
                continue
            return decoder(io.open(p, encoding="utf-8").read()), p
        except Exception:
            continue
    return None, None
