# restart_grid.ps1 - Kill and restart all grid_monitor.py daemons (v8)
#
# Stops every running grid_monitor.py pythonw daemon, then relaunches all
# active pairs x brokers as hidden background processes. Parameters are
# applied from grid_monitor.py PAIR_CONFIG at launch (no params passed here).
#
# Usage (run on VPS after `git pull origin main`):
#   powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\fx_bot\vps\restart_grid.ps1
#
# Options:
#   -IncludeLegacy   also launch No-Go legacy pairs GBPJPY/CHFJPY (default: excluded)
#   -IncludeNZDUSD   also launch NZDUSD (default: excluded - stopped/micro lot)
#   -WhatIf          show what would happen without killing/starting
#
# v8 forward-test set (demo, PF-expectation cleared; configs from PAIR_CONFIG):
#   AUDCAD=20260034 (R-SMA1200+combo)  NZDJPY=20260033 (long-only+combo, carry)
#   EURGBP=20260035 (combo+slot0.5)    AUDNZD=20260036 (R-SMA1200+combo)
#   USDJPY=20260037 (long-only+combo, carry)
#   CADCHF=20260038 (R-SMA1200, correlated cross; screened 2026-06-15, 4th Go pair)
# Legacy/No-Go (excluded by default): GBPJPY=20260031 CHFJPY=20260032 NZDUSD=20260030
#
# Logs: grid_log_{PAIR}_{broker}.txt   State: grid_monitor_state_{PAIR}.json

param(
    [switch]$IncludeLegacy,
    [switch]$IncludeNZDUSD,
    [switch]$WhatIf
)

# UTF-8 output (avoid mojibake in Japanese logs/console)
chcp 65001 > $null
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$ErrorActionPreference = 'Stop'

$pythonw = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe"
$script  = "C:\Users\Administrator\fx_bot\vps\grid_monitor.py"

if (-not (Test-Path $pythonw)) { Write-Error "pythonw not found: $pythonw"; exit 1 }
if (-not (Test-Path $script))  { Write-Error "script not found: $script";  exit 1 }

# Active pairs / brokers (v8 forward-test set)
$pairs = @('AUDCAD','NZDJPY','EURGBP','AUDNZD','USDJPY','CADCHF')
if ($IncludeLegacy) { $pairs = $pairs + @('GBPJPY','CHFJPY') }
if ($IncludeNZDUSD) { $pairs = @('NZDUSD') + $pairs }
$brokers = @('axiory','exness')

function Get-GridProcs {
    Get-CimInstance Win32_Process -Filter "name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*grid_monitor.py*' }
}

# 1) Kill running grid_monitor daemons
Write-Host "=== Stopping running grid_monitor daemons ===" -ForegroundColor Cyan
$running = Get-GridProcs
if (-not $running) {
    Write-Host "  (none running)"
} else {
    foreach ($p in $running) {
        Write-Host ("  kill PID {0}: {1}" -f $p.ProcessId, $p.CommandLine)
        if (-not $WhatIf) { Stop-Process -Id $p.ProcessId -Force }
    }
}
if (-not $WhatIf) { Start-Sleep -Seconds 2 }   # let MT5/handles release

# 2) Relaunch all active pairs x brokers
Write-Host "=== Starting v8 grid daemons ===" -ForegroundColor Cyan
foreach ($pair in $pairs) {
    foreach ($broker in $brokers) {
        Write-Host ("  start: --pair {0} --broker {1}" -f $pair, $broker)
        if (-not $WhatIf) {
            Start-Process -FilePath $pythonw `
                          -ArgumentList $script, '--pair', $pair, '--broker', $broker `
                          -WindowStyle Hidden
            Start-Sleep -Milliseconds 500   # avoid MT5 connect storm
        }
    }
}

# 3) Verify
if (-not $WhatIf) {
    Start-Sleep -Seconds 3
    Write-Host "=== Running grid_monitor daemons ===" -ForegroundColor Cyan
    $expected = $pairs.Count * $brokers.Count
    $now = Get-GridProcs
    $now | Select-Object ProcessId,
        @{N='args';E={ ($_.CommandLine -split 'grid_monitor.py')[1].Trim() }} |
        Format-Table -AutoSize
    Write-Host ("  {0} / {1} expected processes running" -f @($now).Count, $expected) -ForegroundColor Green
}
