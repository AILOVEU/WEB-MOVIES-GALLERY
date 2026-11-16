# -*- coding: utf-8 -*-
"""给 TturritSettingsActivity 加三个下载提速设置行。

改四处：
  1) ROW_ 常量
  2) fillItems()：下载分区插三行
  3) onRowClick()：三行的点击处理
  4) 新增两个选择对话框方法
"""
import io
import sys

P = r"D:\workspace\a_TELE_TURRIT_APP\TMessagesProj\src\main\java\org\telegram\ui\TturritSettingsActivity.java"

# 1) ROW 常量
OLD_ROW = """    /** 同时下载文件个数（默认 3，问题清单 I-033） */
    private static final int ROW_CONCURRENT = 15;
"""
NEW_ROW = """    /** 同时下载文件个数（默认 3，问题清单 I-033） */
    private static final int ROW_CONCURRENT = 15;
    /** 下载提速：非 Premium 也用 8 条连接 */
    private static final int ROW_DL_BOOST = 16;
    /** 大文件分片大小 */
    private static final int ROW_DL_CHUNK = 17;
    /** 下载字节预算 */
    private static final int ROW_DL_BUDGET = 18;
"""

# 2) fillItems：插在 ROW_CONCURRENT 之后
OLD_FILL = """        items.add(UItem.asSettingsCell(ROW_CONCURRENT, R.drawable.msg_download,
                LocaleController.getString(R.string.TTurritConcurrentDownloads),
                String.valueOf(SharedConfig.downloadConcurrentMax)));
"""
NEW_FILL = """        items.add(UItem.asSettingsCell(ROW_CONCURRENT, R.drawable.msg_download,
                LocaleController.getString(R.string.TTurritConcurrentDownloads),
                String.valueOf(SharedConfig.downloadConcurrentMax)));
        items.add(UItem.asSwitchNoIcon(ROW_DL_BOOST, LocaleController.getString(R.string.TTurritDownloadBoost))
                .setChecked(SharedConfig.downloadBoostEnabled));
        items.add(UItem.asShadow(LocaleController.getString(R.string.TTurritDownloadBoostInfo)));
        items.add(UItem.asSettingsCell(ROW_DL_CHUNK, R.drawable.msg_download,
                LocaleController.getString(R.string.TTurritDownloadChunkSize), chunkSizeLabel()));
        items.add(UItem.asSettingsCell(ROW_DL_BUDGET, R.drawable.msg_download,
                LocaleController.getString(R.string.TTurritDownloadByteBudget), byteBudgetLabel()));
        items.add(UItem.asShadow(LocaleController.getString(R.string.TTurritDownloadChunkSizeInfo)
                + "\\n" + LocaleController.getString(R.string.TTurritDownloadByteBudgetInfo)));
"""

# 3) onRowClick：插在 ROW_CONCURRENT 之前
OLD_CLICK = """            case ROW_CONCURRENT:
                showConcurrentDownloadsDialog();
                break;
"""
NEW_CLICK = """            case ROW_CONCURRENT:
                showConcurrentDownloadsDialog();
                break;
            case ROW_DL_BOOST: {
                SharedConfig.downloadBoostEnabled = !SharedConfig.downloadBoostEnabled;
                SharedConfig.saveConfig();
                applyChecked(view, SharedConfig.downloadBoostEnabled);
                // 改的是连接池大小，已在跑的下载要重新起才生效
                toast(LocaleController.getString(R.string.TTurritDownloadBoost));
                break;
            }
            case ROW_DL_CHUNK:
                showChunkSizeDialog();
                break;
            case ROW_DL_BUDGET:
                showByteBudgetDialog();
                break;
"""

# 4) 新方法：插在 showConcurrentDownloadsDialog 之后
OLD_METHOD_END = """        showDialog(builder.create());
    }

    private void exportBanned(boolean onlyBanned) {"""
NEW_METHOD_END = """        showDialog(builder.create());
    }

    /** 分片大小：32..512 KiB，按 4 KiB 对齐的常用档位 */
    private void showChunkSizeDialog() {
        if (getParentActivity() == null) {
            return;
        }
        final int[] values = {32, 64, 128, 256, 512};
        final CharSequence[] labels = new CharSequence[values.length];
        for (int i = 0; i < values.length; i++) {
            labels[i] = values[i] + " KiB";
        }
        AlertDialog.Builder builder = new AlertDialog.Builder(getParentActivity());
        builder.setTitle(LocaleController.getString(R.string.TTurritDownloadChunkSize));
        builder.setItems(labels, (dialog, which) -> {
            SharedConfig.downloadChunkSizeKiB = values[which];
            SharedConfig.saveConfig();
            if (listView != null && listView.adapter != null) {
                listView.adapter.update(false);
            }
        });
        showDialog(builder.create());
    }

    /** 字节预算：0 = 跟随官方，其余为 MiB */
    private void showByteBudgetDialog() {
        if (getParentActivity() == null) {
            return;
        }
        final int[] values = {0, 2, 4, 8, 16, 32};
        final CharSequence[] labels = new CharSequence[values.length];
        for (int i = 0; i < values.length; i++) {
            labels[i] = values[i] == 0
                    ? LocaleController.getString(R.string.TTurritDownloadFollowOfficial)
                    : values[i] + " MiB";
        }
        AlertDialog.Builder builder = new AlertDialog.Builder(getParentActivity());
        builder.setTitle(LocaleController.getString(R.string.TTurritDownloadByteBudget));
        builder.setItems(labels, (dialog, which) -> {
            SharedConfig.downloadByteBudgetMiB = values[which];
            SharedConfig.saveConfig();
            if (listView != null && listView.adapter != null) {
                listView.adapter.update(false);
            }
        });
        showDialog(builder.create());
    }

    private String chunkSizeLabel() {
        return SharedConfig.downloadChunkSizeKiB + " KiB";
    }

    private String byteBudgetLabel() {
        return SharedConfig.downloadByteBudgetMiB == 0
                ? LocaleController.getString(R.string.TTurritDownloadFollowOfficial)
                : SharedConfig.downloadByteBudgetMiB + " MiB";
    }

    private void exportBanned(boolean onlyBanned) {"""


def main():
    with io.open(P, encoding="utf-8") as fh:
        src = fh.read()

    if "ROW_DL_BOOST" in src:
        sys.exit("已打过补丁，跳过")

    for name, old, new in [
        ("ROW 常量", OLD_ROW, NEW_ROW),
        ("fillItems", OLD_FILL, NEW_FILL),
        ("onRowClick", OLD_CLICK, NEW_CLICK),
        ("新方法", OLD_METHOD_END, NEW_METHOD_END),
    ]:
        if old not in src:
            sys.exit("找不到锚点: " + name)
        src = src.replace(old, new, 1)

    with io.open(P, "w", encoding="utf-8", newline="") as fh:
        fh.write(src)
    print("TturritSettingsActivity.java 已更新")


main()
