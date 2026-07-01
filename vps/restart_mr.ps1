# restart_mr.ps1 - Kill and restart the correlation-cross(4h) mean-reversion daemons
#
# Stops every running mr_monitor.py pythonw daemon, then relaunches AUDCAD + CADCHF on
# the demo brokers (axiory + exness) as hidden background processes. Parameters come
# from mr_monitor.py PAIR_CONFIG (no params passed here). Demo lot_scale=1.0
# (BT-comparable). Live is refused by mr_monitor until the forward-test gate clears.
#
# Usage (run on VPS after `git pull origin main`):
#   powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\fx_bot\vps\restart_mr.ps1
#
# Options:
#   -Brokers a,b   override demo brokers (default: axiory,exness)
#   -WhatIf        show what would happen without killing/starting
#
# Strategy: correlation-cross H4 mean-reversion, 3-tier unequal split (0.2/0.3/0.5)
#   + vol-throttle. Per-pair exit (confirmed by BT optimizer/mr_tiered_transfer_bt.py):
#     AUDCAD magic=20260050 / MR_AC / exit A (whole-basket MA) / z_stop 4.5 / vol_th 0.70
#     CADCHF magic=20260051 / MR_CC / exit B (Tier3 partial-TP) / z_stop 4.0 / vol_th 0.90
#   (AUDNZD marginal / EURGBP rejected -> not deployed.)
# Logs: mr_log_{PAIR}_{broker}.txt   State: mr_monitor_state_{PAIR}_{broker}.json

param(
    [string[]]$Brokers = @('axiory','exness'),
    [switch]$WhatIf
)

# UTF-8 output (avoid mojibake in Japanese logs/console)
chcp 65001 > $null
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$ErrorActionPreference = 'Stop'

$pythonw = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\pythonw.exe"
$script  = "C:\Users\Administrator\fx_bot\vps\mr_monitor.py"

if (-not (Test-Path $pythonw)) { Write-Error "pythonw not found: $pythonw"; exit 1 }
if (-not (Test-Path $script))  { Write-Error "script not found: $script";  exit 1 }

$pairs = @('AUDCAD','CADCHF')

function Get-MrProcs {
    Get-CimInstance Win32_Process -Filter "name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*mr_monitor.py*' }
}

# 1) Kill running mr_monitor daemons
Write-Host "=== Stopping running mr_monitor daemons ===" -ForegroundColor Cyan
$running = Get-MrProcs
if (-not $running) {
    Write-Host "  (none running)"
} else {
    foreach ($p in $running) {
        Write-Host ("  kill PID {0}: {1}" -f $p.ProcessId, $p.CommandLine)
        if (-not $WhatIf) { Stop-Process -Id $p.ProcessId -Force }
    }
}
if (-not $WhatIf) { Start-Sleep -Seconds 2 }   # let MT5/handles release

# 2) Relaunch AUDCAD x demo brokers
Write-Host "=== Starting mr_monitor daemons (demo) ===" -ForegroundColor Cyan
foreach ($pair in $pairs) {
    foreach ($broker in $Brokers) {
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
    Write-Host "=== Running mr_monitor daemons ===" -ForegroundColor Cyan
    $expected = $pairs.Count * $Brokers.Count
    $now = Get-MrProcs
    $now | Select-Object ProcessId,
        @{N='args';E={ ($_.CommandLine -split 'mr_monitor.py')[1].Trim() }} |
        Format-Table -AutoSize
    Write-Host ("  {0} / {1} expected processes running" -f @($now).Count, $expected) -ForegroundColor Green
}
