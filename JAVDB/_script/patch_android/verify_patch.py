# -*- coding: utf-8 -*-
"""校验改动后的 Java 文件括号平衡与关键结构。

不用内联 heredoc，避免 PowerShell 转义破坏正则。
"""
import io
import re
import sys

BASE = r"D:\workspace\a_TELE_TURRIT_APP\TMessagesProj\src\main\java\org\telegram"

FILES = [
    (BASE + r"\messenger\SharedConfig.java", "SharedConfig.java"),
    (BASE + r"\messenger\FileLoadOperation.java", "FileLoadOperation.java"),
    (BASE + r"\ui\TturritSettingsActivity.java", "TturritSettingsActivity.java"),
]


def strip_noise(src):
    """去掉注释和字符串字面量，只留结构字符。"""
    out = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            while i < n and src[i] != '\n':
                i += 1
        elif c == '/' and i + 1 < n and src[i + 1] == '*':
            i += 2
            while i + 1 < n and not (src[i] == '*' and src[i + 1] == '/'):
                i += 1
            i += 2
        elif c == '"':
            i += 1
            while i < n:
                if src[i] == '\\':
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                i += 1
        elif c == "'":
            i += 1
            while i < n:
                if src[i] == '\\':
                    i += 2
                    continue
                if src[i] == "'":
                    i += 1
                    break
                i += 1
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def main():
    all_ok = True
    for path, name in FILES:
        src = io.open(path, encoding='utf-8').read()
        clean = strip_noise(src)
        o, c = clean.count('{'), clean.count('}')
        ok = o == c
        all_ok = all_ok and ok
        print("%-32s { %d  } %d   %s" % (name, o, c, "平衡 OK" if ok else "不平衡 FAIL"))
        if not ok:
            depth = 0
            for ln, line in enumerate(clean.split('\n'), 1):
                depth += line.count('{') - line.count('}')
                if depth < 0:
                    print("    首次负深度在第 %d 行: %s" % (ln, line.strip()[:70]))
                    break

    print()
    print("全部平衡:", all_ok)

    # 关键结构检查
    print()
    print("=== 关键结构 ===")
    flo = io.open(FILES[1][0], encoding='utf-8').read()
    checks = [
        ("FileLoadOperation.resolveChunkSize", "private int resolveChunkSize()" in flo),
        ("FileLoadOperation 用 SharedConfig.downloadBoostEnabled", "SharedConfig.downloadBoostEnabled" in flo),
        ("FileLoadOperation 用 downloadChunkSizeKiB", "SharedConfig.downloadChunkSizeKiB" in flo),
        ("FileLoadOperation 用 downloadByteBudgetMiB", "SharedConfig.downloadByteBudgetMiB" in flo),
        ("updateParams 调 resolveChunkSize", "downloadChunkSizeBig = resolveChunkSize();" in flo),
    ]
    sc = io.open(FILES[0][0], encoding='utf-8').read()
    checks += [
        ("SharedConfig 字段 downloadBoostEnabled", "public static boolean downloadBoostEnabled" in sc),
        ("SharedConfig 字段 downloadChunkSizeKiB", "public static int downloadChunkSizeKiB" in sc),
        ("SharedConfig 字段 downloadByteBudgetMiB", "public static int downloadByteBudgetMiB" in sc),
        ("SharedConfig saveConfig 落盘", 'putBoolean("downloadBoostEnabled"' in sc),
        ("SharedConfig loadConfig 读取", 'getBoolean("downloadBoostEnabled"' in sc),
    ]
    ui = io.open(FILES[2][0], encoding='utf-8').read()
    checks += [
        ("UI ROW_DL_BOOST", "ROW_DL_BOOST" in ui),
        ("UI showChunkSizeDialog", "private void showChunkSizeDialog()" in ui),
        ("UI showByteBudgetDialog", "private void showByteBudgetDialog()" in ui),
        ("UI chunkSizeLabel", "private String chunkSizeLabel()" in ui),
        ("UI byteBudgetLabel", "private String byteBudgetLabel()" in ui),
    ]
    for label, ok in checks:
        print("  %-46s %s" % (label, "OK" if ok else "MISSING"))
        all_ok = all_ok and ok

    sys.exit(0 if all_ok else 1)


main()
