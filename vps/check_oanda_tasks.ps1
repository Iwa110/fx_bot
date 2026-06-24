# check_oanda_tasks.ps1 - 退役した demo 'oanda' ブローカーへの参照を洗い出す
#
# 目的: OANDA 端末を実口座(live)へ切替えたため、'--broker oanda'(旧demo)で動く
#       ジョブ/プロセスが残っていないか確認する。残っていても broker_config の
#       enabled=False で接続拒否されるが、無駄起動を避けるため停止推奨。
#       ※ 'oanda_live' / 'oanda_demo' は対象外(末尾アンダースコアで除外)。
#
# 使い方 (VPS で):
#   powershell -ExecutionPolicy Bypass -File C:\Users\Administrator\fx_bot\vps\check_oanda_tasks.ps1
#
# スキャン対象: (1)タスクスケジューラ (2)vps\*.bat の中身 (3)起動中プロセス

chcp 65001 > $null
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

$vpsDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# '--broker oanda' を厳密一致 (oanda_live / oanda_demo は負の先読みで除外)
$rxBroker = '(?i)--broker\s+oanda(?!_)'

$anyHit = $false

# ── (1) タスクスケジューラ ─────────────────────────────────────────────
Write-Host "=== (1) Task Scheduler ===" -ForegroundColor Cyan
try {
    $tasks = Get-ScheduledTask -ErrorAction Stop
} catch {
    Write-Host "  [WARN] Get-ScheduledTask 失敗(管理者権限が必要かも): $($_.Exception.Message)" -ForegroundColor Yellow
    $tasks = @()
}

$taskHit = $false
foreach ($t in $tasks) {
    foreach ($a in $t.Actions) {
        $exe  = [string]$a.Execute
        $arg  = [string]$a.Arguments
        $line = "$exe $arg"
        $direct = $line -match $rxBroker                                  # 引数に直接 --broker oanda
        $batRef = ($line -match '(?i)\.bat') -and ($line -match '(?i)fx_bot')  # fx_bot の .bat を起動
        if ($direct -or $batRef) {
            $anyHit = $true; $taskHit = $true
            $tag = if ($direct) { "[--broker oanda 直接]" } else { "[fx_bot .bat 起動 -> (2)で中身確認]" }
            Write-Host ("  {0}  Task='{1}'  State={2}" -f $tag, $t.TaskName, $t.State) -ForegroundColor Yellow
            Write-Host ("      Action: {0}" -f $line.Trim())
        }
    }
}
if (-not $taskHit) { Write-Host "  (fx_bot 関連 / --broker oanda のタスクは見つかりません)" -ForegroundColor Green }

# ── (2) vps\*.bat の中身 ───────────────────────────────────────────────
Write-Host ""
Write-Host "=== (2) vps\*.bat 内の有効な --broker oanda 行 ===" -ForegroundColor Cyan
$batHit = $false
foreach ($bat in (Get-ChildItem -Path $vpsDir -Filter *.bat -File)) {
    $hits = Select-String -Path $bat.FullName -Pattern $rxBroker
    foreach ($h in $hits) {
        $trim = $h.Line.TrimStart()
        # コメント行(REM / ::)は除外
        if ($trim -match '(?i)^\s*REM\b' -or $trim -match '^\s*::') { continue }
        $anyHit = $true; $batHit = $true
        Write-Host ("  {0}:{1}: {2}" -f $bat.Name, $h.LineNumber, $h.Line.Trim()) -ForegroundColor Yellow
    }
}
if (-not $batHit) { Write-Host "  (有効な --broker oanda 行は .bat に見つかりません)" -ForegroundColor Green }

# ── (3) 起動中プロセス ─────────────────────────────────────────────────
Write-Host ""
Write-Host "=== (3) 起動中プロセスの --broker oanda ===" -ForegroundColor Cyan
$procs = Get-CimInstance Win32_Process -Filter "name='python.exe' OR name='pythonw.exe'" |
         Where-Object { $_.CommandLine -and ($_.CommandLine -match $rxBroker) }
if ($procs) {
    $anyHit = $true
    foreach ($p in $procs) {
        Write-Host ("  PID {0}: {1}" -f $p.ProcessId, $p.CommandLine) -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  停止するには (旧demo oanda プロセスを全Kill):" -ForegroundColor Yellow
    Write-Host '    Get-CimInstance Win32_Process -Filter "name=''pythonw.exe''" |'
    Write-Host '      Where-Object { $_.CommandLine -match ''--broker oanda(?!_)'' } |'
    Write-Host '      ForEach-Object { Stop-Process -Id $_.ProcessId -Force }'
} else {
    Write-Host "  (--broker oanda で動くプロセスはありません)" -ForegroundColor Green
}

# ── 結論 ───────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== 結論 ===" -ForegroundColor Cyan
if (-not $anyHit) {
    Write-Host "  クリーン: --broker oanda の参照なし = go-live して安全。" -ForegroundColor Green
} else {
    Write-Host "  Yellow の .bat / タスク / プロセスを停止・無効化してから go-live してください。" -ForegroundColor Yellow
    Write-Host "  (broker_config の oanda は enabled=False のため、残っていても実害は無駄起動のみ)"
}
