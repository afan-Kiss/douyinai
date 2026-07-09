# Stress / gate test for /api/context and /api/orders - must return within 12s, never HTTP 500.
param(
    [string]$Root = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [string]$BaseUrl = "http://127.0.0.1:8765",
    [int]$Rounds = 5,
    [int]$MaxSec = 12
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

$env:PIGEON_HEADLESS = "1"
$env:PIGEON_PROJECT_ROOT = $Root
$env:PIGEON_NO_CDP = "1"
$env:PIGEON_USE_BROWSER = "0"
Start-Process -FilePath (Join-Path $Root "dist\pigeon-feige.exe") -WorkingDirectory $Root | Out-Null

if (-not (Wait-AcceptanceApiHealth -BaseUrl $BaseUrl -MaxAttempts 60)) {
    Write-Host "FAIL startup: API not ready" -ForegroundColor Red
    exit 1
}
if (Get-Command Wait-AcceptanceBridgeReady -ErrorAction SilentlyContinue) {
    if (-not (Wait-AcceptanceBridgeReady -BaseUrl $BaseUrl -MaxAttempts 40)) {
        Write-Host "WARN bridge not ready - continuing" -ForegroundColor Yellow
        $warns++
    }
}
if (-not (Wait-AcceptanceDaemonReady -MaxAttempts 40)) {
    Write-Host "WARN python daemon slow - continuing" -ForegroundColor Yellow
    $warns++
}
Start-Sleep -Seconds 2

$before = Get-AcceptanceProjectCounts
$failures = 0
$warns = 0

$convPath = "/api/conversations?category=recent" + "&light=1"
$conv = Invoke-JsonGet -Path $convPath -TimeoutSec $MaxSec
$uid = ""
if ($conv.json -and $conv.json.items -and @($conv.json.items).Count -gt 0) {
    $uid = [string]$conv.json.items[0].security_user_id
}
if (-not $uid) {
    $uid = "AQTest0000000000000000000000000000000000000000000000000000000000000001"
    Write-Host "WARN no conversation uid - using synthetic uid for API shape test" -ForegroundColor Yellow
    $warns++
}

for ($i = 1; $i -le $Rounds; $i++) {
    $ctxRes = Invoke-JsonGet -Path ("/api/context?user_id=" + [uri]::EscapeDataString($uid)) -TimeoutSec $MaxSec
    $ordRes = Invoke-JsonGet -Path ("/api/orders?user_id=" + [uri]::EscapeDataString($uid)) -TimeoutSec $MaxSec

    foreach ($pair in @(
            @{ label = "context"; res = $ctxRes }
            @{ label = "orders"; res = $ordRes }
        )) {
        $label = $pair.label
        $res = $pair.res
        $lineOk = $true
        if ($res.code -eq 500) { $lineOk = $false; Write-Host "  FAIL round $i ${label}: HTTP 500" -ForegroundColor Red }
        if ($res.ms -gt ($MaxSec * 1000)) { $lineOk = $false; Write-Host ("  FAIL round {0} {1}: timeout {2}ms" -f $i, $label, $res.ms) -ForegroundColor Red }
        if ($res.error) { $lineOk = $false; Write-Host "  FAIL round $i ${label}: $($res.error)" -ForegroundColor Red }
        if (-not $res.raw) { $lineOk = $false; Write-Host "  FAIL round $i ${label}: empty body" -ForegroundColor Red }
        else {
            try {
                $j = $res.json
                if (-not $j) { $j = $res.raw | ConvertFrom-Json }
                if ($label -eq "context") {
                    if (-not (Test-ContextShape $j)) {
                        $lineOk = $false
                        Write-Host "  FAIL round $i context: bad shape" -ForegroundColor Red
                    }
                }
                else {
                    if (-not (Test-OrdersShape $j)) {
                        $lineOk = $false
                        Write-Host "  FAIL round $i orders: bad shape" -ForegroundColor Red
                    }
                }
            }
            catch {
                $lineOk = $false
                Write-Host "  FAIL round $i ${label}: non-JSON" -ForegroundColor Red
            }
        }
        if (-not $lineOk) { $failures++ }
        else {
            Write-Host ("  round {0} {1}: code={2} ms={3}" -f $i, $label, $res.code, $res.ms)
        }
    }

    if (-not (Test-AcceptanceApiHealth -BaseUrl $BaseUrl)) {
        Write-Host "  FAIL EXE/API died at round $i" -ForegroundColor Red
        $failures++
        break
    }
    Start-Sleep -Milliseconds 300
}

$after = Get-AcceptanceProjectCounts
if ($after.feige -gt 1) { $failures++; Write-Host "FAIL feige count grew" -ForegroundColor Red }
if ($after.python -gt ($before.python + 2)) { $failures++; Write-Host "FAIL python count grew" -ForegroundColor Red }
if ($after.node -gt ($before.node + 2)) { $failures++; Write-Host "FAIL node count grew" -ForegroundColor Red }

Write-Host ("processes: feige={0} python={1} node={2}" -f $after.feige, $after.python, $after.node)

Stop-AcceptanceProjectProcesses

if ($failures -gt 0) {
    Write-Host "OVERALL: FAIL ($failures issues)" -ForegroundColor Red
    exit 1
}
if ($warns -gt 0) {
    Write-Host "OVERALL: WARN" -ForegroundColor Yellow
    exit 0
}
Write-Host "OVERALL: PASS" -ForegroundColor Green
exit 0
