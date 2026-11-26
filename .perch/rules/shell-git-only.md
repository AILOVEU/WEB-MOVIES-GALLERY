# Shell：只用 Git 的 shell，禁止 PowerShell 和系统默认 cmd

Windows 上执行任何脚本、命令、构建或调试操作，一律使用 Git for Windows 提供的 shell。**不得使用 PowerShell，也不得使用系统默认 cmd.exe。** 即使宿主环境的默认 shell 是 PowerShell，也必须把命令包进 Git Bash 里执行。

## 调用方式

```bash
"C:\Program Files\Git\bin\bash.exe" -lc "命令"
```

- 需要加载用户环境（PATH、.profile）时加 `-l`；纯脚本执行可以不加。
- 也可以使用 Git 自带的 `C:\Program Files\Git\git-cmd.exe`。
- 已在本机验证存在的路径：
  - `C:\Program Files\Git\bin\bash.exe`
  - `C:\Program Files\Git\usr\bin\bash.exe`
  - `C:\Program Files\Git\git-cmd.exe`
  - `C:\Program Files\Git\cmd\git.exe`

## 禁止事项

1. 不使用 PowerShell 语法和 cmdlet（`Get-ChildItem`、`Select-String`、`Out-File`、`Test-Path` 等）。
2. 不使用系统默认 `cmd.exe` 执行脚本。
3. 不编写 `.ps1` 脚本，不依赖 PowerShell 的引号规则和管道语义。
4. 不使用 `Out-File -Encoding ascii`（会写入 BOM，导致 `#!/bin/bash: No such file or directory`）。

## 为什么

1. **引号语义不同**：PowerShell 的 `"` `'` 转义规则和 POSIX shell 不一致。
   例如 `adb shell "content query --where \"_display_name='x'\""` 会被拆坏，报 `/system/bin/sh: no closing quote`。
2. **管道语义不同**：PowerShell 的 `|` 传的是对象而不是字节流，参数会被吞掉或改写。
3. **编码问题**：PowerShell 5.1 默认编码不是 UTF-8，中文会被写成 `?`。
4. **重定向破坏二进制**：`adb exec-out ... > file` 在 PowerShell 里会做文本转码，sqlite 文件被破坏后报 `file is not a database`。
5. **语法缺失**：`&&`、`export X=`、heredoc、`/dev/null` 在 PowerShell 5.1 里不可用。

## 正确写法

- 使用 bash 原生语法：`&&`、`||`、`$VAR`、heredoc、`2>/dev/null`。
- 脚本文件一律 UTF-8 无 BOM 写入。
- 中文输出前设置 `export PYTHONIOENCODING=utf-8`。
- 涉及二进制传输（`adb pull` / `adb exec-out`）必须走 bash 重定向。
- 需要写文件时用 bash 的 `>` 或 `printf`，不要用 PowerShell 的 `Out-File`。

## 例外

- `gradlew.bat` 这类 Windows 批处理，直接在 Git Bash 里执行 `./gradlew.bat`（Git Bash 会自动调用 cmd 处理 .bat），不要用 PowerShell，也不要手动开 cmd 窗口。
- 需要 WMI、Windows 服务等系统级操作时，先说明原因并单独确认，不要默认切回 PowerShell。
