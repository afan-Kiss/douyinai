# Bridge hot-path timeout stress — fail fast if EXE/API dies mid-run.
param(
    [string]$BaseUrl = "http://127.0.0.1:8765",
    [int]$Rounds = 20,
    [int]$HotPathTimeoutSec = 12
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
. (Join-Path $Root "scripts\lib\acceptance_helpers.ps1")

function Measure-Case {
    param([string]$Label, [string]$Path)
    $times = @()
    $errors = 0
    $aborted = $false
    for ($i = 1; $i -le $Rounds; $i++) {
        if (-not (Test-AcceptanceApiHealth -BaseUrl $BaseUrl)) {
            Write-Host ("  ABORT {0}: API down at round {1}/{2}" -f $Label, $i, $Rounds) -ForegroundColor Red
            $errors += ($Rounds - $i + 1)
            $aborted = $true
            break
        }
        $counts = Get-AcceptanceProjectCounts
        if ($counts.feige -ne 1) {
            Write-Host ("  ABORT {0}: feige={1} at round {2}/{3}" -f $Label, $counts.feige, $i, $Rounds) -ForegroundColor Red
            $errors += ($Rounds - $i + 1)
            $aborted = $true
            break
        }
        $hit = Invoke-AcceptanceHotPath -BaseUrl $BaseUrl -Path $Path -TimeoutSec $HotPathTimeoutSec
        $times += $hit.ms
        if (-not $hit.ok) {
            $errors++
            if ($errors -ge 3 -and -not (Test-AcceptanceApiHealth -BaseUrl $BaseUrl)) {
                Write-Host ("  ABORT {0}: repeated failures + API down" -f $Label) -ForegroundColor Red
                $errors += ($Rounds - $i)
                $aborted = $true
                break
            }
        }
    }
    if ($times.Count -eq 0) { $times = @(0) }
    $sorted = @($times | Sort-Object)
    $idx = [math]::Max(0, [math]::Ceiling(0.95 * $sorted.Count) - 1)
    return @{
        label   = $Label
        errors  = $errors
        aborted = $aborted
        avg_ms  = [int][math]::Round(($times | Measure-Object -Average).Average)
        p95_ms  = [int]$sorted[$idx]
        max_ms  = [int]$sorted[-1]
    }
}

if (-not (Assert-AcceptanceServiceReady -BaseUrl $BaseUrl)) {
    exit 1
}

if (Get-Command Wait-AcceptanceDaemonReady -ErrorAction SilentlyContinue) {
    Wait-AcceptanceDaemonReady | Out-Null
    Start-Sleep -Milliseconds 300
}

$before = Get-AcceptanceProjectCounts
$cases = @(
    (Measure-Case -Label 'session' -Path '/api/session?light=1')
    (Measure-Case -Label 'process_status' -Path '/api/process/status')
    (Measure-Case -Label 'conversations' -Path '/api/conversations?category=recent&light=1')
)
Start-Sleep -Milliseconds 300
$after = Get-AcceptanceProjectCounts

$severity = 'pass'
foreach ($c in $cases) {
    if ($c.errors -gt 0 -or $c.aborted) { $severity = 'fail'; break }
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
    $abort = if ($c.aborted) { ' aborted' } else { '' }
    Write-Host ("  {0}: errors={1} avg={2}ms p95={3}ms max={4}ms{5}" -f $c.label, $c.errors, $c.avg_ms, $c.p95_ms, $c.max_ms, $abort)
}
Write-Host ("  processes before: feige={0} py={1} node={2}" -f $before.feige, $before.python, $before.node)
Write-Host ("  processes after : feige={0} py={1} node={2}" -f $after.feige, $after.python, $after.node)
if ($severity -eq 'fail') {
    Write-AcceptanceRecoveryDiagnostics -Hint 'bridge timeout FAILED — do not continue stress on dead API'
}
switch ($severity) {
    'warn' { Write-Host 'OVERALL: WARN' -ForegroundColor Yellow }
    'fail' { Write-Host 'OVERALL: FAIL' -ForegroundColor Red }
    default { Write-Host 'OVERALL: PASS' -ForegroundColor Green }
}
if ($severity -eq 'fail') { exit 1 }
exit 0
