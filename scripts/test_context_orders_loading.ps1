# Gate test for /api/context and /api/orders (fast + one heavy round).
param(
    [string]$Root = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [string]$BaseUrl = "http://127.0.0.1:8765",
    [int]$Rounds = 5,
    [int]$ContextMaxMs = 6000,
    [int]$OrdersFastMaxMs = 4000,
    [int]$OrdersHeavyMaxMs = 12200
)

$ErrorActionPreference = "Continue"
$Helpers = Join-Path $Root "scripts\lib\acceptance_helpers.ps1"
if (Test-Path $Helpers) { . $Helpers }

function Invoke-JsonGet {
    param(
        [string]$Path,
        [int]$TimeoutSec = 12
    )
    $url = ($BaseUrl.TrimEnd('/')) + $Path
    $started = Get-Date
    try {
        $resp = Invoke-WebRequest -Uri $url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        $ms = [int]((Get-Date) - $started).TotalMilliseconds
        $json = $null
        try { $json = $resp.Content | ConvertFrom-Json -ErrorAction Stop } catch {}
        return @{
            ok      = ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300)
            code    = [int]$resp.StatusCode
            ms      = $ms
            json    = $json
            raw     = [string]$resp.Content
            error   = ''
            timeout = $false
        }
    }
    catch {
        $ms = [int]((Get-Date) - $started).TotalMilliseconds
        $timeout = ($_.Exception.Message -match 'timed out|timeout')
        return @{
            ok      = $false
            code    = 0
            ms      = $ms
            json    = $null
            raw     = ''
            error   = $_.Exception.Message
            timeout = $timeout
        }
    }
}

function Test-ContextShape {
    param($Json)
    if (-not $Json) { return $false }
    if (-not $Json.context) { return $false }
    $ctx = $Json.context
    if ($null -eq $ctx.messages) { return $false }
    return ($ctx.messages -is [array] -or $ctx.messages -is [System.Collections.IEnumerable])
}

function Test-OrdersShape {
    param($Json)
    if (-not $Json) { return $false }
    if (-not $Json.orders) { return $false }
    $orders = $Json.orders
    if ($null -eq $orders.cards) {
        if ($orders.has_order -eq $false) { return $true }
        return $false
    }
    return ($orders.cards -is [array] -or $orders.cards -is [System.Collections.IEnumerable])
}

Write-Host "=== Context / Orders loading gate ===" -ForegroundColor Cyan
Write-Host "root: $Root"

Stop-AcceptanceProjectProcesses
Start-Sleep -Seconds 3
Remove-Item (Join-Path $Root "logs\runtime\bdms_daemon.lock") -ErrorAction SilentlyContinue

$env:PIGEON_HEADLESS = "1"
$env:PIGEON_PROJECT_ROOT = $Root
$env:PIGEON_NO_CDP = "1"
$env:PIGEON_USE_BROWSER = "0"
Start-Process -FilePath (Join-Path $Root "dist\pigeon-feige.exe") -WorkingDirectory $Root | Out-Null

if (-not (Wait-AcceptanceApiHealth -BaseUrl $BaseUrl -MaxAttempts 60)) {
    Write-Host "FAIL startup: API not ready" -ForegroundColor Red
    $ec = Write-AcceptanceScriptFinal -Label 'context_orders' -ExitCode 1
    exit 1
}
if (-not (Wait-AcceptanceBridgeReady -BaseUrl $BaseUrl -MaxAttempts 40)) {
    Write-Host "FAIL startup: bridge not ready within 20s" -ForegroundColor Red
    $ec = Write-AcceptanceScriptFinal -Label 'context_orders' -ExitCode 1
    exit 1
}
if (-not (Wait-AcceptanceDaemonReady -MaxAttempts 40)) {
    Write-Host "FAIL startup: python daemon not ready within 20s" -ForegroundColor Red
    $ec = Write-AcceptanceScriptFinal -Label 'context_orders' -ExitCode 1
    exit 1
}
Start-Sleep -Seconds 2

$before = Get-AcceptanceProjectCounts
$failures = 0

$convPath = "/api/conversations?category=recent" + "&light=1"
$conv = Invoke-JsonGet -Path $convPath -TimeoutSec 8
$uid = ""
if ($conv.json -and $conv.json.items -and @($conv.json.items).Count -gt 0) {
    $uid = [string]$conv.json.items[0].security_user_id
}
if (-not $uid) {
    $uid = "AQTest0000000000000000000000000000000000000000000000000000000000000001"
    Write-Host "WARN no conversation uid - using synthetic uid" -ForegroundColor Yellow
}

for ($i = 1; $i -le $Rounds; $i++) {
    if (-not (Test-AcceptanceFailFast -BaseUrl $BaseUrl -Hint "round $i")) {
        Write-Host "  FAIL round ${i}: feige/health fail-fast" -ForegroundColor Red
        $failures++
        break
    }

    $ctxRes = Invoke-JsonGet -Path ("/api/context?user_id=" + [uri]::EscapeDataString($uid)) -TimeoutSec 8
    $ordFast = Invoke-JsonGet -Path ("/api/orders?user_id=" + [uri]::EscapeDataString($uid) + "&fast=1") -TimeoutSec 6
    $ordHeavy = $null
    if ($i -eq $Rounds) {
        $ordHeavy = Invoke-JsonGet -Path ("/api/orders?user_id=" + [uri]::EscapeDataString($uid) + "&heavy=1") -TimeoutSec 14
    }

    $roundOk = $true
    if ($ctxRes.code -eq 500 -or $ctxRes.error -or -not $ctxRes.raw) { $roundOk = $false }
    if ($ctxRes.ms -gt $ContextMaxMs) { $roundOk = $false; Write-Host ("  FAIL round {0} context slow: {1}ms" -f $i, $ctxRes.ms) -ForegroundColor Red }
    if (-not (Test-ContextShape $ctxRes.json)) { $roundOk = $false; Write-Host "  FAIL round $i context: bad shape" -ForegroundColor Red }

    if ($ordFast.code -eq 500 -or $ordFast.error -or -not $ordFast.raw) { $roundOk = $false }
    if ($ordFast.ms -gt $OrdersFastMaxMs) { $roundOk = $false; Write-Host ("  FAIL round {0} orders_fast slow: {1}ms" -f $i, $ordFast.ms) -ForegroundColor Red }
    if (-not (Test-OrdersShape $ordFast.json)) { $roundOk = $false; Write-Host "  FAIL round $i orders_fast: bad shape" -ForegroundColor Red }

    $heavyMs = '-'
    if ($ordHeavy) {
        $heavyMs = $ordHeavy.ms
        if ($ordHeavy.code -eq 500 -or $ordHeavy.error -or -not $ordHeavy.raw) { $roundOk = $false }
        if ($ordHeavy.ms -gt $OrdersHeavyMaxMs) { $roundOk = $false; Write-Host ("  FAIL round {0} orders_heavy slow: {1}ms" -f $i, $ordHeavy.ms) -ForegroundColor Red }
        if (-not (Test-OrdersShape $ordHeavy.json)) { $roundOk = $false; Write-Host "  FAIL round $i orders_heavy: bad shape" -ForegroundColor Red }
    }

    $counts = Get-AcceptanceProjectCounts
    if ($counts.feige -ne 1) { $roundOk = $false; Write-Host "  FAIL round ${i}: feige=$($counts.feige)" -ForegroundColor Red }
    if ($counts.python -gt ($before.python + 2)) { $roundOk = $false; Write-Host "  FAIL round ${i}: python grew to $($counts.python)" -ForegroundColor Red }

    if (-not $roundOk) { $failures++ }
    else {
        Write-Host ("  round {0}: context={1}ms orders_fast={2}ms orders_heavy={3}ms feige={4} py={5} node={6}" -f $i, $ctxRes.ms, $ordFast.ms, $heavyMs, $counts.feige, $counts.python, $counts.node)
    }
    Start-Sleep -Milliseconds 300
}

$after = Get-AcceptanceProjectCounts
if ($after.feige -gt 1) { $failures++; Write-Host "FAIL feige count grew" -ForegroundColor Red }
if ($after.python -gt ($before.python + 2)) { $failures++; Write-Host "FAIL python count grew" -ForegroundColor Red }
if ($after.node -gt ($before.node + 2)) { $failures++; Write-Host "FAIL node count grew" -ForegroundColor Red }

Write-Host ("processes: feige={0} python={1} node={2}" -f $after.feige, $after.python, $after.node)
Stop-AcceptanceProjectProcesses

$exitCode = if ($failures -gt 0) { 1 } else { 0 }
Write-AcceptanceScriptFinal -Label 'context_orders' -ExitCode $exitCode | Out-Null
exit $exitCode
