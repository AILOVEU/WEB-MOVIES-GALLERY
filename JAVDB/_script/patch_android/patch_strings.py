# -*- coding: utf-8 -*-
"""给 strings.xml 加下载提速相关的字符串（中文主语言）。

插在 TTurritConcurrentDownloads 之后，保持下载分区聚在一起。
"""
import io
import sys

P = r"D:\workspace\a_TELE_TURRIT_APP\TMessagesProj\src\main\res\values\strings.xml"

ANCHOR = '    <string name="TTurritConcurrentDownloads">同时下载文件个数</string>\n'

NEW = """    <string name="TTurritDownloadBoost">下载提速（8 连接）</string>
    <string name="TTurritDownloadBoostInfo">非 Premium 账号也用满 8 条下载连接。官方默认非 Premium 只给 2 条，打开后请求率会提高约 4 倍，可能触发服务端限流（FLOOD_WAIT），被限流时请关掉。</string>
    <string name="TTurritDownloadChunkSize">下载分片大小</string>
    <string name="TTurritDownloadChunkSizeInfo">大文件每次请求的字节数。官方默认 128 KiB，调到 512 KiB 可把请求数降到 1/4。服务端不接受时客户端会自动回退到 32 KiB。</string>
    <string name="TTurritDownloadByteBudget">下载字节预算</string>
    <string name="TTurritDownloadByteBudgetInfo">同时在途的字节总量上限，0 = 跟随官方（Premium 16 MiB / 非 Premium 2 MiB）。预算越大并发请求越多，占带宽也越凶。</string>
    <string name="TTurritDownloadFollowOfficial">跟随官方</string>
"""


def main():
    with io.open(P, encoding="utf-8") as fh:
        src = fh.read()

    if "TTurritDownloadBoost" in src:
        sys.exit("字符串已存在，跳过")

    if ANCHOR not in src:
        sys.exit("找不到锚点 TTurritConcurrentDownloads")

    src = src.replace(ANCHOR, ANCHOR + NEW, 1)

    with io.open(P, "w", encoding="utf-8", newline="") as fh:
        fh.write(src)
    print("strings.xml 已更新")


main()
