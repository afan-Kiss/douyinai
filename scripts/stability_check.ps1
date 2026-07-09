# Stability inspection + optional stress acceptance for pigeon-feige desktop stack.
# Usage:
#   .\scripts\stability_check.ps1              # snapshot + API check
#   .\scripts\stability_check.ps1 -RunStress    # + 10-round hot-path stress
#   .\scripts\stability_check.ps1 -Json         # machine-readable report
param(
    [string]$BaseUrl = "http://127.0.0.1:8765",
    [int]$StressRounds = 10,
    [switch]$RunStress,
    [switch]$Json,
    [int]$ApiTimeoutSec = 12,
    [int]$StressApiTimeoutSec = 10,
    [int]$MaxFeige = 1,
    [int]$MaxNode = 2,
    [int]$MaxPython = 3
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Helpers = Join-Path $Root "scripts\lib\acceptance_helpers.ps1"
if (Test-Path $Helpers) { . $Helpers }

$ProjectPattern = 'douyin-pigeon-protocol|pigeon-feige|run\.py|go-bridge|pigeon_protocol|run_bdms_daemon\.mjs|run_bdms_fetch\.mjs'

function Get-ProcessCommandLine {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return '' }
    try {
        $row = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -OperationTimeoutSec 8 -ErrorAction Stop
        return [string]$row.CommandLine
    }
    catch {
        return ''
    }
}

function Test-ProjectCommandLine {
    param(
        [string]$Name,
        [string]$CommandLine
    )
    if ($Name -ieq 'pigeon-feige.exe') { return $true }
    if (-not $CommandLine) { return $false }
    return ($CommandLine -match $ProjectPattern)
}

function Get-ProjectWin32Processes {
    $filter = "Name='node.exe' OR Name='python.exe' OR Name='python3.exe' OR Name='pigeon-feige.exe'"
    try {
        $rows = @(Get-CimInstance Win32_Process -Filter $filter -OperationTimeoutSec 20 -ErrorAction Stop)
    }
    catch {
        Write-Warning "Win32_Process filtered query failed: $_ — falling back to Get-Process"
        $rows = @()
        foreach ($n in @('pigeon-feige', 'python', 'python3', 'node')) {
            foreach ($p in @(Get-Process -Name $n -ErrorAction SilentlyContinue)) {
                $rows += [pscustomobject]@{
                    ProcessId      = $p.Id
                    Name           = if ($p.ProcessName -notmatch '\.exe$') { "$($p.ProcessName).exe" } else { $p.ProcessName }
                    CommandLine    = Get-ProcessCommandLine -ProcessId $p.Id
                    WorkingSetSize = [int64]$p.WorkingSet64
                }
            }
        }
        return @($rows | Where-Object { Test-ProjectCommandLine -Name $_.Name -CommandLine $_.CommandLine })
    }

    return @(
        $rows | Where-Object { Test-ProjectCommandLine -Name $_.Name -CommandLine ([string]$_.CommandLine) } |
            ForEach-Object {
                $ws = 0
                try { $ws = [int64](Get-Process -Id $_.ProcessId -ErrorAction Stop).WorkingSet64 } catch {}
                [pscustomobject]@{
                    ProcessId      = [int]$_.ProcessId
                    Name           = [string]$_.Name
                    CommandLine    = [string]$_.CommandLine
                    WorkingSetSize = $ws
                }
            }
    )
}

function Get-ProcessSnapshot {
    param([array]$Rows)

    $feige = @($Rows | Where-Object { $_.Name -ieq 'pigeon-feige.exe' })
    $python = @($Rows | Where-Object { $_.Name -match '^python(\.exe)?$' })
    $node = @($Rows | Where-Object { $_.Name -ieq 'node.exe' })

    $portPid = $null
    $portPids = @()
    try {
        $matches = netstat -ano | Select-String ':8765\s+.*LISTENING'
        foreach ($m in $matches) {
            $parts = ($m.ToString().Trim() -split '\s+')
            if ($parts.Count -ge 5) {
                $portPids += [int]$parts[-1]
            }
        }
        $portPids = @($portPids | Sort-Object -Unique)
        if ($portPids.Count -gt 0) { $portPid = [int]$portPids[0] }
    }
    catch {
        $portPids = @()
    }

    $topMem = @(
        $Rows |
            Sort-Object { [int64]$_.WorkingSetSize } -Descending |
            Select-Object -First 10 |
            ForEach-Object {
                [ordered]@{
                    pid          = [int]$_.ProcessId
                    name         = $_.Name
                    ws_mb        = [math]::Round([int64]$_.WorkingSetSize / 1MB, 1)
                    cmdline_head = if ($_.CommandLine) {
                        $c = [string]$_.CommandLine
                        if ($c.Length -gt 140) { $c.Substring(0, 140) + '...' } else { $c }
                    }
                    else { '' }
                }
            }
    )

    return [ordered]@{
        feige_count   = $feige.Count
        python_count  = $python.Count
        node_count    = $node.Count
        port_8765_pid = $portPid
        port_8765_pids = @($portPids | ForEach-Object { [int]$_ })
        top_memory    = $topMem
        feige_pids    = @($feige | ForEach-Object { [int]$_.ProcessId })
        python_pids   = @($python | ForEach-Object { [int]$_.ProcessId })
        node_pids     = @($node | ForEach-Object { [int]$_.ProcessId })
    }
}

function Get-ApiLatencySeverity {
    param(
        [string]$Label,
        [int]$ElapsedMs,
        [bool]$Ok
    )
    if (-not $Ok) { return 'fail' }
    $key = $Label
    if ($key -eq 'conversations_light') { $key = 'conversations' }
    if ($key -eq 'session_light') { $key = 'session' }
    if ($key -eq 'conversations') {
        if ($ElapsedMs -gt 8000) { return 'fail' }
        if ($ElapsedMs -gt 3000) { return 'warn' }
        return 'pass'
    }
    if ($ElapsedMs -gt 1000) { return 'warn' }
    return 'pass'
}

function Measure-LatencyStats {
    param([int[]]$Samples)
    if (-not $Samples -or $Samples.Count -eq 0) {
        return @{ avg_ms = 0; p95_ms = 0; max_ms = 0 }
    }
    $sorted = @($Samples | Sort-Object)
    $idx = [math]::Ceiling(0.95 * $sorted.Count) - 1
    if ($idx -lt 0) { $idx = 0 }
    if ($idx -ge $sorted.Count) { $idx = $sorted.Count - 1 }
    return @{
        avg_ms = [int][math]::Round(($sorted | Measure-Object -Average).Average)
        p95_ms = [int]$sorted[$idx]
        max_ms = [int]$sorted[-1]
    }
}

function Invoke-ApiCheck {
    param(
        [string]$Path,
        [string]$Label,
        [int]$Retries = 1,
        [int]$TimeoutSec = $ApiTimeoutSec
    )
    $url = ($BaseUrl.TrimEnd('/')) + $Path
    $attempt = 0
    $item = $null
    while ($attempt -le $Retries) {
        $attempt++
        $started = Get-Date
        $item = [ordered]@{
            label      = $Label
            path       = $Path
            url        = $url
            ok         = $false
            status     = 0
            elapsed_ms = 0
            error      = ''
            summary    = ''
            severity   = 'fail'
            attempts   = $attempt
        }
        try {
            $resp = Invoke-WebRequest -Uri $url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
            $code = [int]$resp.StatusCode
            $body = [string]$resp.Content
            $item.status = $code
            $item.elapsed_ms = [int]((Get-Date) - $started).TotalMilliseconds
            if ($code -ge 200 -and $code -lt 300 -and $body) {
                $json = $null
                try {
                    $json = $body | ConvertFrom-Json -ErrorAction Stop
                }
                catch {
                    if ($body -match '"ok"\s*:\s*true') {
                        $item.ok = $true
                        $item.summary = 'ok_regex'
                        $item.severity = Get-ApiLatencySeverity -Label $Label -ElapsedMs $item.elapsed_ms -Ok $true
                    }
                    else {
                        $item.error = "json parse: $($_.Exception.Message)"
                    }
                    if ($item.ok -or $attempt -gt $Retries) { return $item }
                    continue
                }
                if ($null -ne $json.ok) {
                    $item.ok = [bool]$json.ok
                }
                else {
                    $item.ok = $true
                }
                if ($json.node -and $json.node.registered_live -ne $null) {
                    $actual = $json.node.actual_project_live
                    $mismatch = $json.node.mismatch
                    $item.summary = "reg=$($json.node.registered_live) actual=$actual max=$($json.node.max) mismatch=$mismatch"
                }
                elseif ($json.accounts) {
                    $item.summary = "accounts=$($json.accounts.Count)"
                }
                elseif ($json.logged_in -ne $null) {
                    $item.summary = "logged_in=$($json.logged_in)"
                }
                elseif ($null -ne $json.items) {
                    $item.summary = "items=$($json.items.Count)"
                }
                elseif ($json.conversations) {
                    $item.summary = "conversations=$($json.conversations.Count)"
                }
                elseif ($json.error) {
                    $item.summary = "error=$($json.error)"
                }
                else {
                    $item.summary = 'json_ok'
                }
                $item.severity = Get-ApiLatencySeverity -Label $Label -ElapsedMs $item.elapsed_ms -Ok $item.ok
                if ($item.ok -or $attempt -gt $Retries) { return $item }
                if ($json.needs_repair -or $json.error) { continue }
                return $item
            }
            else {
                $item.error = "HTTP $code"
                if ($body) { $item.error += ": $($body.Substring(0, [Math]::Min(180, $body.Length)))" }
                $item.severity = 'fail'
                if ($attempt -le $Retries) { continue }
                return $item
            }
        }
        catch {
            $item.error = $_.Exception.Message
            $item.elapsed_ms = [int]((Get-Date) - $started).TotalMilliseconds
            if ($attempt -le $Retries) { continue }
            return $item
        }
    }
    return $item
}

function Test-ProcessStatusLimits {
    param(
        [object]$ProcessStatusJson,
        [string]$Phase
    )
    $checks = @()
    if (-not $ProcessStatusJson -or -not $ProcessStatusJson.node) { return $checks }
    $n = $ProcessStatusJson.node
    $reg = [int]$n.registered_live
    $max = [int]$n.max
    $checks += [ordered]@{
        phase  = $Phase
        name   = 'registered_live'
        value  = $reg
        limit  = "<= $MaxNode"
        pass   = ($reg -le $MaxNode)
        detail = "max=$max"
    }
    if ($null -ne $n.actual_project_live) {
        $actual = [int]$n.actual_project_live
        $checks += [ordered]@{
            phase  = $Phase
            name   = 'actual_project_live'
            value  = $actual
            limit  = "<= $MaxNode"
            pass   = ($actual -le $MaxNode)
            detail = 'cmdline scan'
        }
        $warnMismatch = [bool]$n.mismatch
        $checks += [ordered]@{
            phase  = $Phase
            name   = 'registry vs actual'
            value  = if ($warnMismatch) { 'mismatch' } else { 'ok' }
            limit  = 'no mismatch'
            pass   = (-not $warnMismatch)
            detail = if ($warnMismatch) { 'WARNING: registered and actual node counts differ' } else { 'ok' }
        }
    }
    return $checks
}

function Test-SnapshotLimits {
    param(
        [hashtable]$Snap,
        [string]$Phase
    )
    $checks = @()

    $checks += [ordered]@{
        phase   = $Phase
        name    = 'pigeon-feige count'
        value   = $Snap.feige_count
        limit   = "<= $MaxFeige"
        pass    = ($Snap.feige_count -le $MaxFeige)
        detail  = "pids=$($Snap.feige_pids -join ',')"
    }
    $checks += [ordered]@{
        phase   = $Phase
        name    = 'project python count'
        value   = $Snap.python_count
        limit   = "<= $MaxPython"
        pass    = ($Snap.python_count -le $MaxPython)
        detail  = "pids=$($Snap.python_pids -join ',')"
    }
    $checks += [ordered]@{
        phase   = $Phase
        name    = 'project node count'
        value   = $Snap.node_count
        limit   = "<= $MaxNode"
        pass    = ($Snap.node_count -le $MaxNode)
        detail  = "pids=$($Snap.node_pids -join ',')"
    }

    $portOk = ($Snap.port_8765_pids.Count -eq 1)
    $checks += [ordered]@{
        phase   = $Phase
        name    = '8765 listeners'
        value   = $Snap.port_8765_pids.Count
        limit   = '= 1'
        pass    = $portOk
        detail  = "pids=$($Snap.port_8765_pids -join ',')"
    }

    if ($Snap.feige_count -eq 1 -and $Snap.port_8765_pids.Count -eq 1) {
        $ownerOk = ($Snap.port_8765_pids[0] -in $Snap.feige_pids)
        $checks += [ordered]@{
            phase   = $Phase
            name    = '8765 owned by feige'
            value   = $Snap.port_8765_pids[0]
            limit   = "in $($Snap.feige_pids -join ',')"
            pass    = $ownerOk
            detail  = if ($ownerOk) { 'ok' } else { 'port held by non-feige pid' }
        }
    }

    return $checks
}

function Invoke-StressSuite {
    param(
        [int]$Rounds,
        [hashtable]$Before
    )
    if (Get-Command Assert-AcceptanceServiceReady -ErrorAction SilentlyContinue) {
        if (-not (Assert-AcceptanceServiceReady -BaseUrl $BaseUrl)) {
            return [ordered]@{
                rounds  = $Rounds
                cases   = @([ordered]@{ label = 'preflight'; severity = 'fail'; pass = $false; fail = 1; ok = 0; avg_ms = 0; p95_ms = 0; max_ms = 0; error = 'API not ready before stress' })
                before  = $Before
                after   = $Before
                growth  = [ordered]@{ feige_delta = 0; python_delta = 0; node_delta = 0; pass = $false }
                aborted = $true
            }
        }
    }
    $cases = @(
        @{ label = 'session_light'; path = '/api/session?light=1' },
        @{ label = 'accounts'; path = '/api/accounts' },
        @{ label = 'conversations_light'; path = '/api/conversations?category=recent&light=1' },
        @{ label = 'process_status'; path = '/api/process/status' },
        @{ label = 'qr_status'; path = '/api/qr-login/status' }
    )
    $results = @()
    $suiteAborted = $false
    foreach ($case in $cases) {
        if ($suiteAborted) { break }
        $okCount = 0
        $failCount = 0
        $times = @()
        for ($i = 1; $i -le $Rounds; $i++) {
            if (Get-Command Test-AcceptanceApiHealth -ErrorAction SilentlyContinue) {
                if (-not (Test-AcceptanceApiHealth -BaseUrl $BaseUrl)) {
                    $failCount += ($Rounds - $i + 1)
                    $suiteAborted = $true
                    break
                }
                $live = Get-AcceptanceProjectCounts
                if ($live.feige -ne 1) {
                    $failCount += ($Rounds - $i + 1)
                    $suiteAborted = $true
                    break
                }
            }
            $hit = Invoke-ApiCheck -Path $case.path -Label $case.label -Retries 0 -TimeoutSec $StressApiTimeoutSec
            $times += [int]$hit.elapsed_ms
            if ($hit.ok) { $okCount++ } else {
                $failCount++
                if ($failCount -ge 3 -and (Get-Command Test-AcceptanceApiHealth -ErrorAction SilentlyContinue) -and -not (Test-AcceptanceApiHealth -BaseUrl $BaseUrl)) {
                    $failCount += ($Rounds - $i)
                    $suiteAborted = $true
                    break
                }
            }
        }
        if ($times.Count -eq 0) { $times = @(0) }
        $stats = Measure-LatencyStats -Samples $times
        $severity = if ($failCount -gt 0 -or $suiteAborted) { 'fail' } else { Get-ApiLatencySeverity -Label $case.label -ElapsedMs $stats.max_ms -Ok $true }
        $results += [ordered]@{
            label      = $case.label
            rounds     = $Rounds
            ok         = $okCount
            fail       = $failCount
            avg_ms     = $stats.avg_ms
            p95_ms     = $stats.p95_ms
            max_ms     = $stats.max_ms
            severity   = $severity
            pass       = ($severity -ne 'fail')
            aborted    = $suiteAborted
        }
        if ($suiteAborted) { break }
    }

    Start-Sleep -Milliseconds 500
    $afterRows = Get-ProjectWin32Processes
    $after = Get-ProcessSnapshot -Rows $afterRows

    $growth = [ordered]@{
        feige_delta  = $after.feige_count - $Before.feige_count
        python_delta = $after.python_count - $Before.python_count
        node_delta   = $after.node_count - $Before.node_count
        pass         = (
            ($after.feige_count -eq 1) -and
            ($after.feige_count - $Before.feige_count -le 0) -and
            ($after.python_count - $Before.python_count -le 0) -and
            ($after.node_count - $Before.node_count -le 0)
        )
    }

    return [ordered]@{
        rounds  = $Rounds
        cases   = $results
        before  = $Before
        after   = $after
        growth  = $growth
        aborted = $suiteAborted
    }
}

$startedAt = Get-Date
if (Get-Command Assert-AcceptanceServiceReady -ErrorAction SilentlyContinue) {
    if (-not (Assert-AcceptanceServiceReady -BaseUrl $BaseUrl)) {
        exit 1
    }
}
if (Get-Command Wait-AcceptanceBridgeReady -ErrorAction SilentlyContinue) {
    if (-not (Wait-AcceptanceBridgeReady -BaseUrl $BaseUrl)) {
        Write-Host '[WARN] bridge not ready before API baseline; conversations may retry' -ForegroundColor Yellow
    }
}
$rows = Get-ProjectWin32Processes
$snapshot = Get-ProcessSnapshot -Rows $rows
$limitChecks = Test-SnapshotLimits -Snap $snapshot -Phase 'baseline'

$apiChecks = @(
    (Invoke-ApiCheck -Path '/api/session?light=1' -Label 'session' -Retries 1),
    (Invoke-ApiCheck -Path '/api/accounts' -Label 'accounts' -Retries 1),
    (Invoke-ApiCheck -Path '/api/process/status' -Label 'process_status' -Retries 0),
    (Invoke-ApiCheck -Path '/api/conversations?category=recent&light=1' -Label 'conversations' -Retries 5)
)

$processStatusJson = $null
$psApi = $apiChecks | Where-Object { $_.label -eq 'process_status' } | Select-Object -First 1
if ($psApi -and $psApi.ok) {
    try {
        $rawPs = curl.exe -sS -m $ApiTimeoutSec http://127.0.0.1:8765/api/process/status 2>$null
        if ($rawPs) { $processStatusJson = $rawPs | ConvertFrom-Json -ErrorAction SilentlyContinue }
    } catch {}
}
if ($processStatusJson) {
    $limitChecks += Test-ProcessStatusLimits -ProcessStatusJson $processStatusJson -Phase 'baseline'
}

$stress = $null
if ($RunStress) {
    if (Get-Command Wait-AcceptanceDaemonReady -ErrorAction SilentlyContinue) {
        if (-not (Wait-AcceptanceDaemonReady)) {
            Write-Host '[WARN] python daemon not ready before stress baseline' -ForegroundColor Yellow
        }
    }
    Start-Sleep -Milliseconds 300
    $rows = Get-ProjectWin32Processes
    $snapshot = Get-ProcessSnapshot -Rows $rows
    $stress = Invoke-StressSuite -Rounds $StressRounds -Before $snapshot
    $limitChecks += Test-SnapshotLimits -Snap $stress.after -Phase 'after_stress'
    if ($processStatusJson) {
        try {
            $rawAfter = curl.exe -sS -m $ApiTimeoutSec http://127.0.0.1:8765/api/process/status 2>$null
            if ($rawAfter) {
                $afterPs = $rawAfter | ConvertFrom-Json -ErrorAction SilentlyContinue
                $limitChecks += Test-ProcessStatusLimits -ProcessStatusJson $afterPs -Phase 'after_stress'
            }
        } catch {}
    }
    $limitChecks += [ordered]@{
        phase  = 'after_stress'
        name   = 'process growth'
        value  = "feige+$($stress.growth.feige_delta) py+$($stress.growth.python_delta) node+$($stress.growth.node_delta)"
        limit  = 'no growth'
        pass   = [bool]$stress.growth.pass
        detail = 'hot-path stress should not leak processes'
    }
}

$apiPass = -not @($apiChecks | Where-Object { $_.severity -eq 'fail' }).Count
$limitPass = -not @($limitChecks | Where-Object { -not $_.pass }).Count
$stressPass = $true
$hasWarn = @($apiChecks | Where-Object { $_.severity -eq 'warn' }).Count -gt 0
if ($stress) {
    $stressPass = ($stress.cases | Where-Object { $_.severity -eq 'fail' }).Count -eq 0 -and [bool]$stress.growth.pass
    if (@($stress.cases | Where-Object { $_.severity -eq 'warn' }).Count -gt 0) { $hasWarn = $true }
}
$hasFail = -not ($apiPass -and $limitPass -and $stressPass)
$overallSeverity = if ($hasFail) { 'fail' } elseif ($hasWarn) { 'warn' } else { 'pass' }

$report = [ordered]@{
    ok           = (-not $hasFail)
    severity     = $overallSeverity
    project_root = $Root
    base_url     = $BaseUrl
    timestamp    = (Get-Date).ToString('o')
    elapsed_ms   = [int]((Get-Date) - $startedAt).TotalMilliseconds
    processes    = $snapshot
    limits       = $limitChecks
    api          = $apiChecks
    stress       = $stress
}

if ($Json) {
    $report | ConvertTo-Json -Depth 8
}
else {
    Write-Host "=== Pigeon stability check ===" -ForegroundColor Cyan
    Write-Host "root: $Root"
    Write-Host "url : $BaseUrl"
    Write-Host ''
    Write-Host '[Processes]' -ForegroundColor Yellow
    Write-Host ("  pigeon-feige.exe : {0}  (pids: {1})" -f $snapshot.feige_count, ($snapshot.feige_pids -join ', '))
    Write-Host ("  project python   : {0}  (pids: {1})" -f $snapshot.python_count, ($snapshot.python_pids -join ', '))
    Write-Host ("  project node     : {0}  (pids: {1})" -f $snapshot.node_count, ($snapshot.node_pids -join ', '))
    Write-Host ("  8765 listener    : pid {0}" -f ($snapshot.port_8765_pids -join ', '))
    Write-Host ''
    Write-Host '  Top memory (project-related):'
    foreach ($p in $snapshot.top_memory) {
        Write-Host ("    PID {0} {1} {2} MB  {3}" -f $p.pid, $p.name, $p.ws_mb, $p.cmdline_head)
    }
    Write-Host ''
    Write-Host '[API]' -ForegroundColor Yellow
    foreach ($a in $apiChecks) {
        $mark = switch ($a.severity) { 'warn' { 'WARN' } 'fail' { 'FAIL' } default { 'PASS' } }
        $color = switch ($a.severity) { 'warn' { 'Yellow' } 'fail' { 'Red' } default { 'Green' } }
        Write-Host ("  [{0}] {1} {2}ms {3} {4}" -f $mark, $a.label, $a.elapsed_ms, $a.summary, $(if ($a.error) { $a.error } else { '' })) -ForegroundColor $color
    }
    Write-Host ''
    Write-Host '[Limits]' -ForegroundColor Yellow
    foreach ($c in $limitChecks) {
        $mark = if ($c.pass) { 'PASS' } else { 'FAIL' }
        $color = if ($c.pass) { 'Green' } else { 'Red' }
        Write-Host ("  [{0}] {1}: {2} (limit {3}) {4}" -f $mark, $c.name, $c.value, $c.limit, $c.detail) -ForegroundColor $color
    }
    if ($stress) {
        Write-Host ''
        Write-Host "[Stress x$StressRounds]" -ForegroundColor Yellow
        if ($stress.aborted) {
            Write-Host '  [FAIL] stress aborted early (API or feige died)' -ForegroundColor Red
        }
        foreach ($s in $stress.cases) {
            $mark = switch ($s.severity) { 'warn' { 'WARN' } 'fail' { 'FAIL' } default { 'PASS' } }
            $color = switch ($s.severity) { 'warn' { 'Yellow' } 'fail' { 'Red' } default { 'Green' } }
            $abortNote = if ($s.aborted) { ' aborted' } else { '' }
            Write-Host ("  [{0}] {1}: ok={2} fail={3} avg={4}ms p95={5}ms max={6}ms{7}" -f $mark, $s.label, $s.ok, $s.fail, $s.avg_ms, $s.p95_ms, $s.max_ms, $abortNote) -ForegroundColor $color
        }
        $g = $stress.growth
        $gmark = if ($g.pass) { 'PASS' } else { 'FAIL' }
        $gcolor = if ($g.pass) { 'Green' } else { 'Red' }
        Write-Host ("  [{0}] growth feige+{1} python+{2} node+{3} (feige after={4})" -f $gmark, $g.feige_delta, $g.python_delta, $g.node_delta, $stress.after.feige_count) -ForegroundColor $gcolor
        if (-not $g.pass -and (Get-Command Write-AcceptanceRecoveryDiagnostics -ErrorAction SilentlyContinue)) {
            Write-AcceptanceRecoveryDiagnostics -Hint 'stress growth check failed'
        }
    }
    Write-Host ''
    switch ($overallSeverity) {
        'warn' { Write-Host 'OVERALL: WARN' -ForegroundColor Yellow }
        'fail' { Write-Host 'OVERALL: FAIL' -ForegroundColor Red }
        default { Write-Host 'OVERALL: PASS' -ForegroundColor Green }
    }
}

if ($overallSeverity -eq 'fail') {
    Write-AcceptanceScriptFinal -Label 'stability' -ExitCode 1 | Out-Null
    exit 1
}
Write-AcceptanceScriptFinal -Label 'stability' -ExitCode 0 | Out-Null
exit 0
