# GUI mode smoke — verify WebView EXE stays alive for 60s with API responsive.
param(
    [string]$Root = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [int]$WarmSec = 60,
    [int]$MaxTotalSec = 90
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
    param([string]$Path, [int]$TimeoutSec = 8)
    $url = "http://127.0.0.1:8765$Path"
    try {
        $resp = Invoke-WebRequest -Uri $url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        $code = [int]$resp.StatusCode
        $body = [string]$resp.Content
        $ok = ($code -ge 200 -and $code -lt 300 -and $body.Length -gt 0)
        return @{ ok = $ok; code = $code; body = $body; error = if ($ok) { '' } else { "HTTP $code" } }
    }
    catch {
        return @{ ok = $false; code = 0; error = $_.Exception.Message }
    }
}

function Test-BridgeFromHealth([hashtable]$HealthResp) {
    if (-not $HealthResp.ok) { return $false }
    return ($HealthResp.body -match '"via"\s*:\s*"go/bridge"' -and $HealthResp.body -match '"ok"\s*:\s*true')
}

function Wait-BridgeReady {
    for ($i = 0; $i -lt 15; $i++) {
        if (-not (Get-Process pigeon-feige -ErrorAction SilentlyContinue)) { return $false }
        $h = Invoke-SmokeApi -Path "/api/health" -TimeoutSec 5
        if (Test-BridgeFromHealth $h) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Write-GuiCrashDiagnostics {
    Write-Host ""
    Write-Host "=== GUI crash diagnostics ===" -ForegroundColor Red
    Write-Host "=== processes ==="
    Get-Process pigeon-feige -ErrorAction SilentlyContinue | Format-Table Id, ProcessName, StartTime -AutoSize
    if (Get-Command Get-AcceptanceProjectCounts -ErrorAction SilentlyContinue) {
        $c = Get-AcceptanceProjectCounts
        Write-Host ("feige={0} python={1} node={2}" -f $c.feige, $c.python, $c.node)
    }
    Write-Host "=== port 8765 ==="
    Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue |
        Select-Object State, OwningProcess | Format-Table -AutoSize
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
    Write-Host "=== runtime logs ==="
    $rt = Join-Path $Root "logs\runtime"
    if (Test-Path $rt) {
        Get-ChildItem $rt -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 10 Name, LastWriteTime, Length |
            Format-Table -AutoSize
    }
    $stderr = Join-Path $rt "feige_gui_stderr.txt"
    if (Test-Path $stderr) {
        Write-Host "--- feige_gui_stderr tail ---"
        Get-Content $stderr -Tail 30 -ErrorAction SilentlyContinue
    }
}

Write-Host "=== GUI smoke test ===" -ForegroundColor Cyan
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
    Write-GuiCrashDiagnostics
    exit 1
}

Start-Process -FilePath $exe -WorkingDirectory $Root | Out-Null
Start-Sleep -Seconds 3
$healthReady = $false
$lastHealth = $null
for ($i = 0; $i -lt 30; $i++) {
    if (-not (Get-Process pigeon-feige -ErrorAction SilentlyContinue)) {
        Fail-Now "pigeon-feige exited before health ready (round $i)"
        break
    }
    $h = Invoke-SmokeApi -Path "/api/health" -TimeoutSec 5
    $lastHealth = $h
    if ($h.ok) { $healthReady = $true; break }
    Start-Sleep -Milliseconds 500
}

if (-not $healthReady -and -not $script:fail) {
    Fail-Now "API /api/health not ready within startup window"
}

if (-not $script:fail) {
    if (Test-BridgeFromHealth $lastHealth) {
        Write-Host "bridge OK (via health)" -ForegroundColor Green
    }
    elseif (-not (Wait-BridgeReady)) {
        Fail-Now "bridge not ready (health missing go/bridge)"
    }
    else {
        Write-Host "bridge OK" -ForegroundColor Green
    }
    if (-not $script:fail) {
        Write-Host "waiting ${WarmSec}s for GUI stability..." -ForegroundColor Green
    }
}

if (-not $script:fail) {
    $deadline = (Get-Date).AddSeconds($WarmSec)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-TimeBudget 8)) {
            Fail-Now "exceeded max total time ${MaxTotalSec}s during warm"
            break
        }
        if (-not (Get-Process pigeon-feige -ErrorAction SilentlyContinue)) {
            Fail-Now "pigeon-feige exited during ${WarmSec}s warm period"
            break
        }
        Start-Sleep -Seconds 5
    }
}

$checks = @(
    @{ label = "health"; path = "/api/health" },
    @{ label = "session"; path = "/api/session?light=1" },
    @{ label = "accounts"; path = "/api/accounts" },
    @{ label = "process_status"; path = "/api/process/status" }
)

foreach ($c in $checks) {
    if ($script:fail) { break }
    $r = Invoke-SmokeApi -Path $c.path -TimeoutSec 8
    if ($r.ok) {
        Write-Host ("  [PASS] {0}" -f $c.label) -ForegroundColor Green
    }
    else {
        Fail-Now ("{0} failed: code={1} err={2}" -f $c.label, $r.code, $r.error)
    }
}

if (-not (Get-Process pigeon-feige -ErrorAction SilentlyContinue)) {
    Fail-Now "pigeon-feige not running after warm period"
}

if (Get-Command Get-AcceptanceProjectCounts -ErrorAction SilentlyContinue) {
    $counts = Get-AcceptanceProjectCounts
    Write-Host ("  processes: feige={0} python={1} node={2}" -f $counts.feige, $counts.python, $counts.node)
    if ($counts.feige -ne 1) {
        Fail-Now ("expected feige=1, got feige={0}" -f $counts.feige)
    }
}

if (-not $script:fail) {
    Write-Host "  closing GUI (CloseMainWindow)..." -ForegroundColor Green
    $close = Wait-GuiGracefulExit -CloseWaitSec 15 -Retries 4
    if ($close.ok) {
        Write-Host "  [PASS] $($close.message)" -ForegroundColor Green
    }
    else {
        Fail-Now ("GUI close failed: $($close.message)")
        Get-Process pigeon-feige -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
if ($script:fail) {
    Write-GuiCrashDiagnostics
    Write-Host "OVERALL: FAIL ($script:failReason)" -ForegroundColor Red
    exit 1
}

Write-Host "OVERALL: PASS (GUI alive ${WarmSec}s, API OK)" -ForegroundColor Green
exit 0
