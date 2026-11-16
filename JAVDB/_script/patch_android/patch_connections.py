# -*- coding: utf-8 -*-
"""把「下载提速」布尔开关改成可调的「下载连接数」，默认 8。

为什么是 2 的幂：FileLoadOperation.java:2500 用位掩码轮转连接
    connectionType = ... | ((requestsCount & (downloadConnectionsCount - 1)) << 16)
注释（2499 行）明确写 "Both pool sizes are powers of two"。
非 2 的幂会让掩码算出越界槽位，所以取值限定 1/2/4/8，并在读取时向下取到 2 的幂。
"""
import io
import sys

BASE = r"D:\workspace\a_TELE_TURRIT_APP\TMessagesProj\src\main"

SC = BASE + r"\java\org\telegram\messenger\SharedConfig.java"
FLO = BASE + r"\java\org\telegram\messenger\FileLoadOperation.java"
UI = BASE + r"\java\org\telegram\ui\TturritSettingsActivity.java"
STR = BASE + r"\res\values\strings.xml"

# ---------- SharedConfig ----------
SC_OLD_FIELD = """    /**
     * 下载提速开关（方案 B）：打开后不再按 Premium 区分连接数，
     * 非 Premium 账号也用 ConnectionsManager.DownloadConnectionsCount（8）条下载连接。
     * 默认关：保持官方行为，避免非 Premium 账号被服务端限速。
     */
    public static boolean downloadBoostEnabled = false;
"""

SC_NEW_FIELD = """    /**
     * 下载连接数（方案 B）：大文件下载时同时使用的独立 TCP 连接条数。
     * 官方按 Premium 区分（Premium 8 / 非 Premium 2），这里改成用户可调，默认 8。
     *
     * 必须是 2 的幂：FileLoadOperation 用位掩码轮转连接槽位
     * （requestsCount & (downloadConnectionsCount - 1)），非 2 的幂会算出越界槽位。
     * 合法值 1/2/4/8，读取时向下取到最近的 2 的幂。
     *
     * 注意：连接数越多请求率越高，非 Premium 账号可能触发服务端限流（FLOOD_WAIT）。
     */
    public static int downloadConnections = 8;
"""

SC_OLD_SAVE = '                editor.putBoolean("downloadBoostEnabled", downloadBoostEnabled);\n'
SC_NEW_SAVE = '                editor.putInt("downloadConnections", downloadConnections);\n'

SC_OLD_LOAD = '            downloadBoostEnabled = preferences.getBoolean("downloadBoostEnabled", false);\n'
SC_NEW_LOAD = ('            downloadConnections = floorPowerOfTwo(\n'
               '                    preferences.getInt("downloadConnections", 8), 1, 8);\n')

# 在 SharedConfig 里加 floorPowerOfTwo 辅助方法：插在 saveConfig 之前
SC_OLD_HELPER_ANCHOR = "    public static void saveConfig() {\n"
SC_NEW_HELPER = """    /**
     * 向下取到最近的 2 的幂，并钳在 [min, max] 内。
     * 连接数必须满足这个约束，否则位掩码轮转会越界。
     */
    public static int floorPowerOfTwo(int value, int min, int max) {
        int v = Math.max(min, Math.min(max, value));
        int p = 1;
        while ((p << 1) <= v) {
            p <<= 1;
        }
        return p;
    }

    public static void saveConfig() {
"""

# ---------- FileLoadOperation ----------
FLO_OLD = """                    boolean premium = UserConfig.getInstance(currentAccount).isPremium();
                    // 方案 B：打开下载提速后不再按 Premium 区分连接数，非 Premium 也用满 8 条。
                    // 默认关，保持官方行为（非 Premium 2 条），避免触发服务端限速。
                    boolean boost = SharedConfig.downloadBoostEnabled;
                    downloadConnectionsCount = (premium || boost)
                            ? ConnectionsManager.DownloadConnectionsCount : 2;
                    int byteBudget = SharedConfig.downloadByteBudgetMiB > 0
                            ? SharedConfig.downloadByteBudgetMiB * 1024 * 1024
                            : (premium || boost ? 16 : 2) * 1024 * 1024;
"""

FLO_NEW = """                    boolean premium = UserConfig.getInstance(currentAccount).isPremium();
                    // 方案 B：连接数改由自定义设置决定（默认 8），不再按 Premium 区分。
                    // 值已在 SharedConfig 里钳成 2 的幂，位掩码轮转才安全。
                    downloadConnectionsCount = SharedConfig.downloadConnections;
                    // 字节预算：用户显式设过就用用户的，否则按连接数等比放大。
                    // 官方非 Premium 是 2 连接 / 2 MiB，Premium 是 8 连接 / 16 MiB，
                    // 即每连接约 1-2 MiB，这里沿用 2 MiB/连接 的比例。
                    int byteBudget = SharedConfig.downloadByteBudgetMiB > 0
                            ? SharedConfig.downloadByteBudgetMiB * 1024 * 1024
                            : Math.max(2, downloadConnections * 2) * 1024 * 1024;
"""

# ---------- UI ----------
UI_OLD_ROW = """    /** 下载提速：非 Premium 也用 8 条连接 */
    private static final int ROW_DL_BOOST = 16;
"""
UI_NEW_ROW = """    /** 下载连接数（默认 8，可调 1/2/4/8） */
    private static final int ROW_DL_CONNS = 16;
"""

UI_OLD_FILL = """        items.add(UItem.asSwitchNoIcon(ROW_DL_BOOST, LocaleController.getString(R.string.TTurritDownloadBoost))
                .setChecked(SharedConfig.downloadBoostEnabled));
        items.add(UItem.asShadow(LocaleController.getString(R.string.TTurritDownloadBoostInfo)));
"""
UI_NEW_FILL = """        items.add(UItem.asSettingsCell(ROW_DL_CONNS, R.drawable.msg_download,
                LocaleController.getString(R.string.TTurritDownloadConnections), connectionsLabel()));
        items.add(UItem.asShadow(LocaleController.getString(R.string.TTurritDownloadConnectionsInfo)));
"""

UI_OLD_CLICK = """            case ROW_DL_BOOST: {
                SharedConfig.downloadBoostEnabled = !SharedConfig.downloadBoostEnabled;
                SharedConfig.saveConfig();
                applyChecked(view, SharedConfig.downloadBoostEnabled);
                // 改的是连接池大小，已在跑的下载要重新起才生效
                toast(LocaleController.getString(R.string.TTurritDownloadBoost));
                break;
            }
"""
UI_NEW_CLICK = """            case ROW_DL_CONNS:
                showDownloadConnectionsDialog();
                break;
"""

UI_OLD_METHOD = """    /** 分片大小：32..512 KiB，按 4 KiB 对齐的常用档位 */
    private void showChunkSizeDialog() {
"""
UI_NEW_METHOD = """    /**
     * 下载连接数：只能选 2 的幂。
     * FileLoadOperation 用位掩码轮转连接槽位，非 2 的幂会越界。
     */
    private void showDownloadConnectionsDialog() {
        if (getParentActivity() == null) {
            return;
        }
        final int[] values = {1, 2, 4, 8};
        final CharSequence[] labels = new CharSequence[values.length];
        for (int i = 0; i < values.length; i++) {
            labels[i] = String.valueOf(values[i]);
        }
        AlertDialog.Builder builder = new AlertDialog.Builder(getParentActivity());
        builder.setTitle(LocaleController.getString(R.string.TTurritDownloadConnections));
        builder.setItems(labels, (dialog, which) -> {
            SharedConfig.downloadConnections = values[which];
            SharedConfig.saveConfig();
            if (listView != null && listView.adapter != null) {
                listView.adapter.update(false);
            }
        });
        showDialog(builder.create());
    }

    private String connectionsLabel() {
        return String.valueOf(SharedConfig.downloadConnections);
    }

    /** 分片大小：32..512 KiB，按 4 KiB 对齐的常用档位 */
    private void showChunkSizeDialog() {
"""

# ---------- strings ----------
STR_OLD = """    <string name="TTurritDownloadBoost">下载提速（8 连接）</string>
    <string name="TTurritDownloadBoostInfo">非 Premium 账号也用满 8 条下载连接。官方默认非 Premium 只给 2 条，打开后请求率会提高约 4 倍，可能触发服务端限流（FLOOD_WAIT），被限流时请关掉。</string>
"""
STR_NEW = """    <string name="TTurritDownloadConnections">下载连接数</string>
    <string name="TTurritDownloadConnectionsInfo">大文件下载时同时使用的独立连接条数，默认 8。官方按 Premium 区分（Premium 8 / 非 Premium 2），这里可自行调整。只能选 1/2/4/8：客户端按 2 的幂做连接槽位轮转。连接数越多请求率越高，非 Premium 账号可能触发服务端限流（FLOOD_WAIT），被限流时请调小。</string>
"""


def patch(path, pairs, label):
    src = io.open(path, encoding="utf-8").read()
    for old, new in pairs:
        if old not in src:
            sys.exit("[%s] 找不到锚点:\n%s" % (label, old[:160]))
        src = src.replace(old, new, 1)
    io.open(path, "w", encoding="utf-8", newline="").write(src)
    print("[%s] OK" % label)


def main():
    patch(SC, [
        (SC_OLD_FIELD, SC_NEW_FIELD),
        (SC_OLD_SAVE, SC_NEW_SAVE),
        (SC_OLD_LOAD, SC_NEW_LOAD),
        (SC_OLD_HELPER_ANCHOR, SC_NEW_HELPER),
    ], "SharedConfig")

    patch(FLO, [(FLO_OLD, FLO_NEW)], "FileLoadOperation")

    patch(UI, [
        (UI_OLD_ROW, UI_NEW_ROW),
        (UI_OLD_FILL, UI_NEW_FILL),
        (UI_OLD_CLICK, UI_NEW_CLICK),
        (UI_OLD_METHOD, UI_NEW_METHOD),
    ], "TturritSettingsActivity")

    patch(STR, [(STR_OLD, STR_NEW)], "strings.xml")

    print("\n全部完成")


main()
