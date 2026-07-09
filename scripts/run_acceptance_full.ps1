# Full acceptance runner — any sub-test failure => exit 1.
param(
    [string]$Root = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
)

$ErrorActionPreference = "Continue"
. (Join-Path $Root "scripts\lib\acceptance_helpers.ps1")

$results = @{}

function Run-Step {
    param([string]$Name, [scriptblock]$Block)
    & $Block
    $results[$Name] = $LASTEXITCODE
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ABORT: $Name failed (exit=$LASTEXITCODE)" -ForegroundColor Red
        return $false
    }
    return $true
}

function Start-AcceptanceStack {
    Stop-AcceptanceProjectProcesses
    Start-Sleep -Seconds 3
    Remove-Item (Join-Path $Root "logs\runtime\bdms_daemon.lock") -ErrorAction SilentlyContinue
    for ($w = 0; $w -lt 15; $w++) {
        if (Test-Port8765Released) { break }
        Start-Sleep -Seconds 1
    }
    $env:PIGEON_HEADLESS = '1'
    $env:PIGEON_PROJECT_ROOT = $Root
    $env:PIGEON_ROOT = $Root
    Remove-Item Env:PIGEON_KEEP_API_ON_WEBVIEW_EXIT -ErrorAction SilentlyContinue
    Start-AcceptanceExe
    if (-not (Wait-AcceptanceApiHealth -MaxAttempts 60)) { return $false }
    if (-not (Wait-AcceptanceBridgeReady -MaxAttempts 40)) { return $false }
    if (-not (Wait-AcceptanceDaemonReady -MaxAttempts 40)) { return $false }
    Start-Sleep -Seconds 2
    return $true
}

Push-Location (Join-Path $Root "desktop\pigeon-feige")
go build -o ..\..\dist\pigeon-feige.exe .
$buildCode = $LASTEXITCODE
Pop-Location
if ($buildCode -ne 0) {
    Write-Host "FAIL: go build" -ForegroundColor Red
    exit 1
}

if (-not (Run-Step 'context_orders' { & (Join-Path $Root "scripts\test_context_orders_loading.ps1") })) {
    exit (Write-AcceptanceFullFinal -Results $results)
}

if (-not (Start-AcceptanceStack)) {
    $results['stability'] = 1
    exit (Write-AcceptanceFullFinal -Results $results)
}

if (-not (Run-Step 'stability' { & (Join-Path $Root "scripts\stability_check.ps1") -RunStress -StressRounds 20 })) {
    Stop-AcceptanceProjectProcesses
    exit (Write-AcceptanceFullFinal -Results $results)
}

if (-not (Start-AcceptanceStack)) {
    $results['bridge'] = 1
    exit (Write-AcceptanceFullFinal -Results $results)
}

if (-not (Run-Step 'bridge' { & (Join-Path $Root "scripts\test_bridge_timeout.ps1") })) {
    Stop-AcceptanceProjectProcesses
    exit (Write-AcceptanceFullFinal -Results $results)
}

Stop-AcceptanceProjectProcesses
Start-Sleep -Seconds 5

if (-not (Run-Step 'gui_smoke' { & (Join-Path $Root "scripts\test_gui_smoke.ps1") })) {
    Stop-AcceptanceProjectProcesses
    exit (Write-AcceptanceFullFinal -Results $results)
}

if (-not (Run-Step 'gui_close' { & (Join-Path $Root "scripts\test_gui_close.ps1") })) {
    Stop-AcceptanceProjectProcesses
    exit (Write-AcceptanceFullFinal -Results $results)
}

if (-not (Run-Step 'pure' { & (Join-Path $Root "scripts\test_pure_protocol_acceptance.ps1") })) {
    Stop-AcceptanceProjectProcesses
    exit (Write-AcceptanceFullFinal -Results $results)
}

Push-Location $Root
python .\scripts\test_account_isolation.py
if ($LASTEXITCODE -ne 0) { $results['pure'] = 1 }
python .\scripts\test_conversation_name_parse.py
if ($LASTEXITCODE -ne 0) { $results['pure'] = 1 }
python .\scripts\test_process_guard_smoke.py
if ($LASTEXITCODE -ne 0) { $results['pure'] = 1 }
Pop-Location

Stop-AcceptanceProjectProcesses
exit (Write-AcceptanceFullFinal -Results $results)
