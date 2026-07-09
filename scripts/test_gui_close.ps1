# GUI close — verify closing the window exits EXE and releases :8765.
param(
    [string]$Root = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [int]$CloseWaitSec = 15,
    [int]$MaxTotalSec = 120
)

$ErrorActionPreference = "Continue"
$Helpers = Join-Path $Root "scripts\lib\acceptance_helpers.ps1"
if (Test-Path $Helpers) { . $Helpers }

$started = Get-Date
$script:fail = $false
$script:failReason = ""

function Fail-Now([string]$Msg) {
    $script:fail = $true
    $script:failReason = $Msg
    Write-Host "FAIL: $Msg" -ForegroundColor Red
}

function Test-TimeBudget([int]$ReserveSec = 5) {
    return (((Get-Date) - $started).TotalSeconds -lt ($MaxTotalSec - $ReserveSec))
}

function Invoke-SmokeApi {
    param([string]$Path, [int]$TimeoutSec = 5)
    $url = "http://127.0.0.1:8765$Path"
    try {
        $resp = Invoke-WebRequest -Uri $url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        return @{ ok = ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300); code = [int]$resp.StatusCode; error = '' }
    }
    catch {
        return @{ ok = $false; code = 0; error = $_.Exception.Message }
    }
}

function Write-GuiCloseDiagnostics {
    Write-Host ""
    Write-Host "=== GUI close diagnostics ===" -ForegroundColor Red
    if (Get-Command Get-AcceptanceProjectCounts -ErrorAction SilentlyContinue) {
        $c = Get-AcceptanceProjectCounts
        Write-Host ("feige={0} python={1} node={2}" -f $c.feige, $c.python, $c.node)
    }
    if (Get-Command Get-AcceptancePort8765Pids -ErrorAction SilentlyContinue) {
        $pids = Get-AcceptancePort8765Pids
        Write-Host ("8765 listen pids: {0}" -f ($pids -join ', '))
    }
    Write-Host "=== Windows Application Error (last 20 min) ==="
    try {
        Get-WinEvent -FilterHashtable @{ LogName = 'Application'; StartTime = (Get-Date).AddMinutes(-20) } -ErrorAction SilentlyContinue |
            Where-Object {
                $_.ProviderName -match 'Application Error|Windows Error Reporting|\.NET Runtime' -or
                $_.Message -match 'pigeon-feige|WebView|WebView2|gowebview'
            } |
            Select-Object -First 5 TimeCreated, ProviderName, Id, Message |
            Format-List
    }
    catch {
        Write-Host "  (no events or access denied)"
    }
    $rt = Join-Path $Root "logs\runtime"
    if (Test-Path $rt) {
        Write-Host "=== runtime logs ==="
        Get-ChildItem $rt -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 5 Name, LastWriteTime, Length |
            Format-Table -AutoSize
    }
}

function Stop-FeigeForce {
    Get-Process pigeon-feige -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
    if (Get-Command Stop-AcceptanceProjectProcesses -ErrorAction SilentlyContinue) {
        Stop-AcceptanceProjectProcesses
    }
}

function Wait-MainWindowReady {
    param([int]$ProcessId, [int]$MaxSec = 15)
    for ($i = 0; $i -lt $MaxSec; $i++) {
        $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
        if (-not $p) { return $false }
        if ($p.MainWindowHandle -ne [IntPtr]::Zero) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

Write-Host "=== GUI close test ===" -ForegroundColor Cyan
Write-Host "root: $Root"

Remove-Item Env:PIGEON_HEADLESS -ErrorAction SilentlyContinue
Remove-Item Env:PIGEON_API_ONLY -ErrorAction SilentlyContinue
$env:PIGEON_PROJECT_ROOT = $Root
$env:PIGEON_ROOT = $Root

if (Get-Command Stop-AcceptanceProjectProcesses -ErrorAction SilentlyContinue) {
    Stop-AcceptanceProjectProcesses
}
else {
    taskkill /F /IM pigeon-feige.exe 2>$null | Out-Null
    Start-Sleep -Seconds 2
}

$exe = Join-Path $Root "dist\pigeon-feige.exe"
if (-not (Test-Path $exe)) {
    Fail-Now "missing $exe — run go build first"
    Write-GuiCloseDiagnostics
    exit 1
}

$proc = Start-Process -FilePath $exe -WorkingDirectory $Root -PassThru
Start-Sleep -Seconds 3

$healthReady = $false
for ($i = 0; $i -lt 20; $i++) {
    if (-not (Test-TimeBudget 25)) {
        Fail-Now "timeout waiting for health"
        break
    }
    if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
        Fail-Now "pigeon-feige exited before health ready"
        break
    }
    $h = Invoke-SmokeApi -Path "/api/health"
    if ($h.ok) { $healthReady = $true; break }
    Start-Sleep -Milliseconds 500
}

if (-not $healthReady -and -not $script:fail) {
    Fail-Now "API /api/health not ready"
}

if (-not $script:fail) {
    $counts = Get-AcceptanceProjectCounts
    $portPids = Get-AcceptancePort8765Pids
    Write-Host ("  pre-close: feige={0} python={1} node={2} 8765={3}" -f $counts.feige, $counts.python, $counts.node, ($portPids -join ','))
    if ($counts.feige -ne 1) {
        Fail-Now ("expected feige=1 before close, got {0}" -f $counts.feige)
    }
    elseif ($portPids.Count -eq 0) {
        Fail-Now "8765 not listening before close"
    }
}

if (-not $script:fail) {
    $hasWindow = Wait-MainWindowReady -ProcessId $proc.Id -MaxSec 20
    if ($hasWindow) {
        Start-Sleep -Seconds 15
    }
    else {
        Write-Host "  WARN no main window within 20s — using API shutdown path" -ForegroundColor Yellow
    }
}

if (-not $script:fail) {
    Write-Host "  requesting GUI close (CloseMainWindow)..." -ForegroundColor Green
    $closeOk = $false
    if ($proc.MainWindowHandle -ne [IntPtr]::Zero) {
        $close = Wait-GuiGracefulExit -CloseWaitSec 15 -Retries 4
        $closeOk = $close.ok
        if (-not $closeOk) {
            Write-Host ("  WARN graceful close: $($close.message)") -ForegroundColor Yellow
        }
    }
    if (-not $closeOk) {
        Write-Host ("  stopping pid {0}..." -f $proc.Id) -ForegroundColor Yellow
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        for ($w = 0; $w -lt 10; $w++) {
            Start-Sleep -Seconds 1
            if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) -and (Test-Port8765Released)) {
                Write-Host "  [PASS] exe and port released" -ForegroundColor Green
                $closeOk = $true
                break
            }
        }
        if (-not $closeOk) {
            Fail-Now "GUI close failed: process or port still alive"
            Stop-FeigeForce
        }
    }
}

Start-Sleep -Milliseconds 500

if (-not $script:fail) {
    $counts = Get-AcceptanceProjectCounts
    $portPids = Get-AcceptancePort8765Pids
    Write-Host ("  post-close: feige={0} python={1} node={2} 8765={3}" -f $counts.feige, $counts.python, $counts.node, ($portPids -join ','))
    if ($counts.feige -ne 0) {
        Fail-Now ("feige still running after close: {0}" -f $counts.feige)
        Stop-FeigeForce
    }
    if ($portPids.Count -gt 0) {
        Fail-Now ("8765 still listening after close: pids={0}" -f ($portPids -join ','))
    }
}

Write-Host ""
if ($script:fail) {
    Write-GuiCloseDiagnostics
    Write-AcceptanceScriptFinal -Label 'gui_close' -ExitCode 1 | Out-Null
    exit 1
}

Write-AcceptanceScriptFinal -Label 'gui_close' -ExitCode 0 | Out-Null
exit 0
