# Bridge hot-path timeout stress — 20 rounds per endpoint.
param(
    [string]$BaseUrl = "http://127.0.0.1:8765",
    [int]$Rounds = 20
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ProjectPattern = 'douyin-pigeon-protocol|pigeon-feige|run\.py|go-bridge|pigeon_protocol|run_bdms_daemon\.mjs|run_bdms_fetch\.mjs'

function Get-ProjectCounts {
    $filter = "Name='node.exe' OR Name='python.exe' OR Name='python3.exe' OR Name='pigeon-feige.exe'"
    $rows = @(Get-CimInstance Win32_Process -Filter $filter -OperationTimeoutSec 20 -ErrorAction SilentlyContinue)
    $feige = 0; $py = 0; $node = 0
    foreach ($r in $rows) {
        $cmd = [string]$r.CommandLine
        $name = [string]$r.Name
        if ($name -ieq 'pigeon-feige.exe') { $feige++ ; continue }
        if (-not ($cmd -match $ProjectPattern)) { continue }
        if ($name -ieq 'node.exe') { $node++ }
        elseif ($name -match '^python') { $py++ }
    }
    return @{ feige = $feige; python = $py; node = $node }
}

function Invoke-HotPath {
    param([string]$Path)
    $url = ($BaseUrl.TrimEnd('/')) + $Path
    $started = Get-Date
    $raw = curl.exe -sS -m 30 -w "`n%{http_code}" $url 2>&1
    $ms = [int]((Get-Date) - $started).TotalMilliseconds
    $lines = @($raw -split "`n")
    $code = if ($lines.Count -ge 2) { [int]$lines[-1] } else { 0 }
    return @{ ms = $ms; code = $code; ok = ($code -ge 200 -and $code -lt 300) }
}

function Measure-Case {
    param([string]$Label, [string]$Path)
    $times = @(); $errors = 0
    for ($i = 1; $i -le $Rounds; $i++) {
        $hit = Invoke-HotPath -Path $Path
        $times += $hit.ms
        if (-not $hit.ok) { $errors++ }
    }
    $sorted = @($times | Sort-Object)
    $idx = [math]::Max(0, [math]::Ceiling(0.95 * $sorted.Count) - 1)
    return @{
        label = $Label
        errors = $errors
        avg_ms = [int][math]::Round(($times | Measure-Object -Average).Average)
        p95_ms = [int]$sorted[$idx]
        max_ms = [int]$sorted[-1]
    }
}

$health = curl.exe -s -m 3 http://127.0.0.1:8765/api/health 2>$null
if (-not $health) {
    Write-Host 'FAIL: EXE/API not running on 8765' -ForegroundColor Red
    exit 1
}

$before = Get-ProjectCounts
$cases = @(
    (Measure-Case -Label 'session' -Path '/api/session?light=1')
    (Measure-Case -Label 'process_status' -Path '/api/process/status')
    (Measure-Case -Label 'conversations' -Path '/api/conversations?category=recent&light=1')
)
Start-Sleep -Milliseconds 300
$after = Get-ProjectCounts

$severity = 'pass'
foreach ($c in $cases) {
    if ($c.errors -gt 0) { $severity = 'fail'; break }
    if ($c.label -eq 'conversations') {
        if ($c.max_ms -gt 8000) { $severity = 'fail'; break }
        if ($c.max_ms -gt 3000 -and $severity -ne 'fail') { $severity = 'warn' }
    }
    elseif ($c.max_ms -gt 1000 -and $severity -ne 'fail') { $severity = 'warn' }
}
if ($after.feige -ne 1) { $severity = 'fail' }
if ($after.node -gt 2) { $severity = 'fail' }
if ($after.python -gt $before.python) { $severity = 'fail' }

Write-Host '=== Bridge timeout stress ===' -ForegroundColor Cyan
foreach ($c in $cases) {
    Write-Host ("  {0}: errors={1} avg={2}ms p95={3}ms max={4}ms" -f $c.label, $c.errors, $c.avg_ms, $c.p95_ms, $c.max_ms)
}
Write-Host ("  processes before: feige={0} py={1} node={2}" -f $before.feige, $before.python, $before.node)
Write-Host ("  processes after : feige={0} py={1} node={2}" -f $after.feige, $after.python, $after.node)
switch ($severity) {
    'warn' { Write-Host 'OVERALL: WARN' -ForegroundColor Yellow }
    'fail' { Write-Host 'OVERALL: FAIL' -ForegroundColor Red }
    default { Write-Host 'OVERALL: PASS' -ForegroundColor Green }
}
if ($severity -eq 'fail') { exit 1 }
exit 0
