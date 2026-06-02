# restart_grid.ps1 - Kill and restart all grid_monitor.py daemons (v7)
#
# Stops every running grid_monitor.py pythonw daemon, then relaunches all
# active pairs x brokers as hidden background processes. Parameters are
# applied from grid_monitor.py PAIR_CONFIG at launch (no params passed here).
#
# Usage (run on VPS after `git pull origin main`):
#   powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\fx_bot\vps\restart_grid.ps1
#
# Options:
#   -IncludeNZDUSD   also launch NZDUSD (default: excluded - stopped/micro lot)
#   -WhatIf          show what would happen without killing/starting
#
# Active pairs (v7, demo): GBPJPY / CHFJPY / NZDJPY / AUDCAD on axiory + exness
#   GBPJPY=20260031  CHFJPY=20260032  NZDJPY=20260033  AUDCAD=20260034
#   NZDUSD=20260030 (stopped)
#
# Logs: grid_log_{PAIR}_{broker}.txt   State: grid_monitor_state_{PAIR}.json

param(
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

# Active pairs / brokers
$pairs = @('GBPJPY','CHFJPY','NZDJPY','AUDCAD')
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
Write-Host "=== Starting v7 grid daemons ===" -ForegroundColor Cyan
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
