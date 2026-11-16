# -*- coding: utf-8 -*-
"""给 SharedConfig 增加三个下载提速配置项（方案 B + 分片大小可调）。

新增：
  downloadBoostEnabled     是否启用下载提速（非 Premium 也走 8 连接 + 大字节预算）
  downloadChunkSizeKiB     大文件分片大小（KiB），默认 128，可设 512
  downloadByteBudgetMiB    下载字节预算（MiB），默认 0 = 跟随官方（Premium 16 / 非 2）

只做三处插入：字段声明、saveConfig、loadConfig。全部 UTF-8 读写。
"""
import io
import re
import sys

P = r"D:\workspace\a_TELE_TURRIT_APP\TMessagesProj\src\main\java\org\telegram\messenger\SharedConfig.java"

FIELDS = """
    /**
     * 下载提速开关（方案 B）：打开后不再按 Premium 区分连接数，
     * 非 Premium 账号也用 ConnectionsManager.DownloadConnectionsCount（8）条下载连接。
     * 默认关：保持官方行为，避免非 Premium 账号被服务端限速。
     */
    public static boolean downloadBoostEnabled = false;
    /**
     * 大文件分片大小（KiB）。官方默认 128；调到 512 可把请求数降到 1/4。
     * 合法值 32..512，必须是 4096 的整数倍（Telegram 要求 limit 为 4KiB 对齐）。
     * 服务端若返回 LIMIT_INVALID，FileLoadOperation 会自动回退到 32KiB。
     */
    public static int downloadChunkSizeKiB = 128;
    /**
     * 下载字节预算（MiB）。0 = 跟随官方（Premium 16 / 非 Premium 2）。
     * 非 0 时按此值计算并发请求数 = 预算 / 分片大小。
     */
    public static int downloadByteBudgetMiB = 0;
"""

SAVE = """                editor.putBoolean("downloadBoostEnabled", downloadBoostEnabled);
                editor.putInt("downloadChunkSizeKiB", downloadChunkSizeKiB);
                editor.putInt("downloadByteBudgetMiB", downloadByteBudgetMiB);
"""

LOAD = """            downloadBoostEnabled = preferences.getBoolean("downloadBoostEnabled", false);
            downloadChunkSizeKiB = Math.max(32, Math.min(512, preferences.getInt("downloadChunkSizeKiB", 128)));
            downloadByteBudgetMiB = Math.max(0, Math.min(64, preferences.getInt("downloadByteBudgetMiB", 0)));
"""


def main():
    with io.open(P, encoding="utf-8") as fh:
        src = fh.read()

    # 1) 字段声明：插在 downloadConcurrentMax 之后
    anchor_field = "    public static int downloadConcurrentMax = 3;\n"
    if anchor_field not in src:
        sys.exit("找不到字段锚点 downloadConcurrentMax")
    if "downloadBoostEnabled" in src:
        sys.exit("字段已存在，跳过")
    src = src.replace(anchor_field, anchor_field + FIELDS, 1)

    # 2) saveConfig：插在 downloadConcurrentMax 的 putInt 之后
    anchor_save = '                editor.putInt("downloadConcurrentMax", downloadConcurrentMax);\n'
    if anchor_save not in src:
        sys.exit("找不到 saveConfig 锚点")
    src = src.replace(anchor_save, anchor_save + SAVE, 1)

    # 3) loadConfig：插在 downloadConcurrentMax 的读取之后
    anchor_load = ('            downloadConcurrentMax = Math.max(1, Math.min(10, '
                   'preferences.getInt("downloadConcurrentMax", 3)));\n')
    if anchor_load not in src:
        sys.exit("找不到 loadConfig 锚点")
    src = src.replace(anchor_load, anchor_load + LOAD, 1)

    with io.open(P, "w", encoding="utf-8", newline="") as fh:
        fh.write(src)
    print("SharedConfig.java 已更新")


main()
