# -*- coding: utf-8 -*-
"""import 即生效：把 stdout/stderr 固定成 UTF-8，并屏蔽「管道被提前关掉」的写失败。

要堵的两个坑（都实测撞过）：
  1. Windows 上 stdout 被管道/重定向（`... | tail`、`>> xxx.log`）时编码退回 locale 码
     （简体中文 = GBK），日志里的 ✅/⚠ 会直接 UnicodeEncodeError 把长任务砸死
     → reconfigure 成 UTF-8 + `errors="replace"`（真遇到怪字符只变 '?'，不中断抓取）。
  2. `python x.py stats | head -2` 这种提前关管道的用法，写 stdout 会抛
     `OSError: [Errno 22] Invalid argument` → 再包一层“写失败就吞掉”。
     打印只是诊断信息（都落 `_logs/`），写不出去绝不该让脚本带着 traceback 退场。

用法：在脚本顶部 `import win_utf8`（本目录 = _script/，Python 会自动把脚本所在目录放进
sys.path[0]，所以同目录直接 import 得到）。
"""
import sys


def _utf8(s):
    try:
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return s


def _swallow_broken_pipe(s):
    """包一层代理：stdout/stderr 已被关掉时写操作静默忽略，不抛 traceback。"""

    class _SafeStream:
        def __init__(self, inner):
            self._inner = inner

        def write(self, data):
            try:
                return self._inner.write(data)
            except (OSError, ValueError):
                return len(data) if isinstance(data, str) else 0

        def flush(self):
            try:
                self._inner.flush()
            except (OSError, ValueError):
                pass

        def isatty(self):
            try:
                return self._inner.isatty()
            except Exception:
                return False

        def __getattr__(self, k):
            return getattr(self._inner, k)

    return _SafeStream(s)


for _name in ("stdout", "stderr"):
    _s = getattr(sys, _name, None)
    if _s is not None:
        try:
            setattr(sys, _name, _swallow_broken_pipe(_utf8(_s)))
        except Exception:
            pass
