# -*- coding: utf-8 -*-
"""给 FileLoadOperation 接入自定义下载配置（方案 B + 分片大小可调）。

改三处：
  1) updateParams()：分片大小改读 SharedConfig.downloadChunkSizeKiB
  2) start() 里的 useDownloadByteBudget 分支：连接数与字节预算改读 SharedConfig
  3) 新增 resolveChunkSize/resolveConnections 辅助方法

设计原则：默认值必须与官方行为完全一致，用户不打开开关时零影响。
"""
import io
import sys

P = r"D:\workspace\a_TELE_TURRIT_APP\TMessagesProj\src\main\java\org\telegram\messenger\FileLoadOperation.java"

OLD_UPDATE = """    private void updateParams() {
        if ((preloadPrefixSize > 0 || MessagesController.getInstance(currentAccount).getfileExperimentalParams) && !forceSmallChunk) {
            downloadChunkSizeBig = 1024 * 512;
            maxDownloadRequests = 8;
            maxDownloadRequestsBig = 8;
        } else {
            downloadChunkSizeBig = 1024 * 128;
            maxDownloadRequests = 4;
            maxDownloadRequestsBig = 4;
        }
        maxCdnParts = (int) (FileLoader.DEFAULT_MAX_FILE_SIZE / downloadChunkSizeBig);
    }"""

NEW_UPDATE = """    private void updateParams() {
        if ((preloadPrefixSize > 0 || MessagesController.getInstance(currentAccount).getfileExperimentalParams) && !forceSmallChunk) {
            downloadChunkSizeBig = 1024 * 512;
            maxDownloadRequests = 8;
            maxDownloadRequestsBig = 8;
        } else {
            // 自定义分片大小（设置 -> TTurrit -> 下载分片大小）。默认 128 KiB，与官方一致。
            downloadChunkSizeBig = resolveChunkSize();
            maxDownloadRequests = 4;
            maxDownloadRequestsBig = 4;
        }
        maxCdnParts = (int) (FileLoader.DEFAULT_MAX_FILE_SIZE / downloadChunkSizeBig);
    }

    /**
     * 大文件分片大小：读自定义设置，钳到 Telegram 允许的范围。
     *
     * upload.getFile 的 limit 必须是 4096 的整数倍且不超过 512 KiB，
     * 所以这里按 KiB 取值后左移 10 位，再向下对齐到 4 KiB。
     */
    private int resolveChunkSize() {
        int kib = SharedConfig.downloadChunkSizeKiB;
        if (kib < 32) {
            kib = 32;
        } else if (kib > 512) {
            kib = 512;
        }
        int bytes = kib * 1024;
        return bytes - (bytes % 4096);
    }"""

OLD_BUDGET = """                useDownloadByteBudget = !MessagesController.getInstance(currentAccount).downloadImprovementKillswitch &&
                        totalBytesCount > 0 && key == null && !encryptFile && !isAudioDocument && stream == null &&
                        !isPreloadVideoOperation && !supportsPreloading && preloadPrefixSize == 0;
                if (useDownloadByteBudget) {
                    boolean premium = UserConfig.getInstance(currentAccount).isPremium();
                    downloadConnectionsCount = premium ? ConnectionsManager.DownloadConnectionsCount : 2;
                    int byteBudget = (premium ? 16 : 2) * 1024 * 1024;
                    currentMaxDownloadRequests = byteBudget / currentDownloadChunkSize;
                }"""

NEW_BUDGET = """                useDownloadByteBudget = !MessagesController.getInstance(currentAccount).downloadImprovementKillswitch &&
                        totalBytesCount > 0 && key == null && !encryptFile && !isAudioDocument && stream == null &&
                        !isPreloadVideoOperation && !supportsPreloading && preloadPrefixSize == 0;
                if (useDownloadByteBudget) {
                    boolean premium = UserConfig.getInstance(currentAccount).isPremium();
                    // 方案 B：打开下载提速后不再按 Premium 区分连接数，非 Premium 也用满 8 条。
                    // 默认关，保持官方行为（非 Premium 2 条），避免触发服务端限速。
                    boolean boost = SharedConfig.downloadBoostEnabled;
                    downloadConnectionsCount = (premium || boost)
                            ? ConnectionsManager.DownloadConnectionsCount : 2;
                    int byteBudget = SharedConfig.downloadByteBudgetMiB > 0
                            ? SharedConfig.downloadByteBudgetMiB * 1024 * 1024
                            : (premium || boost ? 16 : 2) * 1024 * 1024;
                    currentMaxDownloadRequests = Math.max(1, byteBudget / currentDownloadChunkSize);
                }"""


def main():
    with io.open(P, encoding="utf-8") as fh:
        src = fh.read()

    if "resolveChunkSize" in src:
        sys.exit("已打过补丁，跳过")

    if OLD_UPDATE not in src:
        sys.exit("找不到 updateParams 原文，可能已被改动")
    src = src.replace(OLD_UPDATE, NEW_UPDATE, 1)

    if OLD_BUDGET not in src:
        sys.exit("找不到 useDownloadByteBudget 原文")
    src = src.replace(OLD_BUDGET, NEW_BUDGET, 1)

    with io.open(P, "w", encoding="utf-8", newline="") as fh:
        fh.write(src)
    print("FileLoadOperation.java 已更新")


main()
