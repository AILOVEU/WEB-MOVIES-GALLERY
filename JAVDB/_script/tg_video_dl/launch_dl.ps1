# 脱离终端启动 Telegram 视频下载作业（避免被 Ctrl+C 连带杀掉）
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File launch_dl.ps1 [-Parts 1] [-Workers 1] [-Scan 0]
# [own] 分类=download 自写下载器启动脚本；原住 a_TELE_TURRIT_APP/Tools/download，2026-11-03 迁至 JAVDB\_script\tg_video_dl
param(
    [int]$Parts = 1,
    [int]$Workers = 1,
    [int]$Scan = 0,
    [int]$Stall = 90,
    [string]$Log = ""
)

# 路径全部由脚本自身位置推导，以后再搬位置不用改这里
$here  = $PSScriptRoot                                  # ...\JAVDB\_script\tg_video_dl
$javdb = Split-Path -Parent (Split-Path -Parent $here)  # ...\JAVDB
if (-not $Log) { $Log = Join-Path $here 'dl_auto.log' }

$exe   = Join-Path $here '.venv\Scripts\python.exe'
$py    = Join-Path $here 'tg_dl.py'
$codes = Join-Path (Join-Path $javdb 'assets') 'videos'
$err   = $Log -replace '\.log$', '.err.log'

$argList = @($py, '--target', 'Sgp987', '--scan', "$Scan", '--codes', $codes,
             '--workers', "$Workers", '--parts', "$Parts", '--stall', "$Stall")

$p = Start-Process -FilePath $exe -ArgumentList $argList `
    -WorkingDirectory $here `
    -WindowStyle Hidden -RedirectStandardOutput $Log -RedirectStandardError $err -PassThru

Write-Host ('launched pid=' + $p.Id + ' parts=' + $Parts + ' workers=' + $Workers + ' log=' + $Log)
