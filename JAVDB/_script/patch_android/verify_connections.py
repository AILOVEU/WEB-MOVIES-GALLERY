# -*- coding: utf-8 -*-
"""校验「下载连接数」改动：括号平衡 + 关键结构 + 旧符号已清除。"""
import io
import sys

BASE = r"D:\workspace\a_TELE_TURRIT_APP\TMessagesProj\src\main"
SC = BASE + r"\java\org\telegram\messenger\SharedConfig.java"
FLO = BASE + r"\java\org\telegram\messenger\FileLoadOperation.java"
UI = BASE + r"\java\org\telegram\ui\TturritSettingsActivity.java"
STR = BASE + r"\res\values\strings.xml"


def strip_noise(src):
    out, i, n = [], 0, len(src)
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
    ok = True
    for path, name in [(SC, "SharedConfig"), (FLO, "FileLoadOperation"), (UI, "TturritSettingsActivity")]:
        src = io.open(path, encoding="utf-8").read()
        clean = strip_noise(src)
        o, c = clean.count('{'), clean.count('}')
        good = o == c
        ok = ok and good
        print("%-26s { %d  } %d   %s" % (name, o, c, "平衡 OK" if good else "不平衡 FAIL"))

    print()
    sc = io.open(SC, encoding="utf-8").read()
    flo = io.open(FLO, encoding="utf-8").read()
    ui = io.open(UI, encoding="utf-8").read()
    st = io.open(STR, encoding="utf-8").read()

    checks = [
        ("SharedConfig 新字段 downloadConnections", "public static int downloadConnections = 8;" in sc),
        ("SharedConfig 旧字段 downloadBoostEnabled 已清除", "downloadBoostEnabled" not in sc),
        ("SharedConfig floorPowerOfTwo 辅助方法", "public static int floorPowerOfTwo(int value, int min, int max)" in sc),
        ("SharedConfig saveConfig 落盘", 'putInt("downloadConnections", downloadConnections)' in sc),
        ("SharedConfig loadConfig 读取+钳位", 'floorPowerOfTwo(' in sc and 'getInt("downloadConnections", 8)' in sc),
        ("FileLoadOperation 用 downloadConnections", "downloadConnectionsCount = SharedConfig.downloadConnections;" in flo),
        ("FileLoadOperation 旧 boost 引用已清除", "downloadBoostEnabled" not in flo),
        ("FileLoadOperation 字节预算按连接数", "Math.max(2, downloadConnections * 2)" in flo),
        ("UI ROW_DL_CONNS", "ROW_DL_CONNS" in ui),
        ("UI 旧 ROW_DL_BOOST 已清除", "ROW_DL_BOOST" not in ui),
        ("UI showDownloadConnectionsDialog", "private void showDownloadConnectionsDialog()" in ui),
        ("UI connectionsLabel", "private String connectionsLabel()" in ui),
        ("UI 选项 1/2/4/8", "final int[] values = {1, 2, 4, 8};" in ui),
        ("strings TTurritDownloadConnections", 'name="TTurritDownloadConnections"' in st),
        ("strings 旧 TTurritDownloadBoost 已清除", 'name="TTurritDownloadBoost"' not in st),
    ]
    print("=== 关键结构 ===")
    for label, good in checks:
        print("  %-46s %s" % (label, "OK" if good else "FAIL"))
        ok = ok and good

    # XML 合法性
    import xml.etree.ElementTree as ET
    try:
        ET.parse(STR)
        print("\nstrings.xml XML 合法: OK")
    except Exception as e:
        print("\nstrings.xml 解析失败:", e)
        ok = False

    sys.exit(0 if ok else 1)


main()
