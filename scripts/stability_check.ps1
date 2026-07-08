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
    [int]$ApiTimeoutSec = 30,
    [int]$MaxFeige = 1,
    [int]$MaxNode = 2,
    [int]$MaxPython = 3
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

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

function Invoke-ApiCheck {
    param(
        [string]$Path,
        [string]$Label,
        [int]$Retries = 1
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
            attempts   = $attempt
        }
        try {
            $raw = curl.exe -sS -m $ApiTimeoutSec -w "`n%{http_code}" $url 2>&1
            $lines = @($raw -split "`n")
            if ($lines.Count -lt 2) {
                $item.error = "empty response"
                if ($attempt -le $Retries) { continue }
                return $item
            }
            $code = [int]$lines[-1]
            $body = ($lines[0..($lines.Count - 2)] -join "`n").Trim()
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
                    $item.summary = "node_live=$($json.node.registered_live)/$($json.node.max)"
                }
                elseif ($json.accounts) {
                    $item.summary = "accounts=$($json.accounts.Count)"
                }
                elseif ($json.logged_in -ne $null) {
                    $item.summary = "logged_in=$($json.logged_in)"
                }
                elseif ($json.items) {
                    $item.summary = "items=$($json.items.Count)"
                }
                elseif ($json.conversations) {
                    $item.summary = "conversations=$($json.conversations.Count)"
                }
                else {
                    $item.summary = 'json_ok'
                }
                return $item
            }
            else {
                $item.error = "HTTP $code"
                if ($body) { $item.error += ": $($body.Substring(0, [Math]::Min(180, $body.Length)))" }
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
    $cases = @(
        @{ label = 'session_light'; path = '/api/session?light=1' },
        @{ label = 'accounts'; path = '/api/accounts' },
        @{ label = 'conversations_light'; path = '/api/conversations?category=recent&light=1' },
        @{ label = 'qr_status'; path = '/api/qr-login/status' }
    )
    $results = @()
    foreach ($case in $cases) {
        $okCount = 0
        $failCount = 0
        $maxMs = 0
        for ($i = 1; $i -le $Rounds; $i++) {
            $hit = Invoke-ApiCheck -Path $case.path -Label $case.label
            if ($hit.ok) { $okCount++ } else { $failCount++ }
            if ($hit.elapsed_ms -gt $maxMs) { $maxMs = $hit.elapsed_ms }
        }
        $results += [ordered]@{
            label      = $case.label
            rounds     = $Rounds
            ok         = $okCount
            fail       = $failCount
            max_ms     = $maxMs
            pass       = ($failCount -eq 0)
        }
    }

    Start-Sleep -Milliseconds 500
    $afterRows = Get-ProjectWin32Processes
    $after = Get-ProcessSnapshot -Rows $afterRows

    $growth = [ordered]@{
        feige_delta  = $after.feige_count - $Before.feige_count
        python_delta = $after.python_count - $Before.python_count
        node_delta   = $after.node_count - $Before.node_count
        pass         = (
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
    }
}

$startedAt = Get-Date
$rows = Get-ProjectWin32Processes
$snapshot = Get-ProcessSnapshot -Rows $rows
$limitChecks = Test-SnapshotLimits -Snap $snapshot -Phase 'baseline'

$apiChecks = @(
    (Invoke-ApiCheck -Path '/api/session?light=1' -Label 'session' -Retries 1),
    (Invoke-ApiCheck -Path '/api/accounts' -Label 'accounts' -Retries 1),
    (Invoke-ApiCheck -Path '/api/process/status' -Label 'process_status' -Retries 0),
    (Invoke-ApiCheck -Path '/api/conversations?category=recent&light=1' -Label 'conversations' -Retries 1)
)

$stress = $null
if ($RunStress) {
    $stress = Invoke-StressSuite -Rounds $StressRounds -Before $snapshot
    $limitChecks += Test-SnapshotLimits -Snap $stress.after -Phase 'after_stress'
    $limitChecks += [ordered]@{
        phase  = 'after_stress'
        name   = 'process growth'
        value  = "feige+$($stress.growth.feige_delta) py+$($stress.growth.python_delta) node+$($stress.growth.node_delta)"
        limit  = 'no growth'
        pass   = [bool]$stress.growth.pass
        detail = 'hot-path stress should not leak processes'
    }
}

$apiPass = -not @($apiChecks | Where-Object { -not $_.ok }).Count
$limitPass = -not @($limitChecks | Where-Object { -not $_.pass }).Count
$stressPass = $true
if ($stress) {
    $stressPass = ($stress.cases | Where-Object { -not $_.pass }).Count -eq 0 -and [bool]$stress.growth.pass
}

$report = [ordered]@{
    ok           = ($apiPass -and $limitPass -and $stressPass)
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
        $mark = if ($a.ok) { 'PASS' } else { 'FAIL' }
        $color = if ($a.ok) { 'Green' } else { 'Red' }
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
        foreach ($s in $stress.cases) {
            $mark = if ($s.pass) { 'PASS' } else { 'FAIL' }
            $color = if ($s.pass) { 'Green' } else { 'Red' }
            Write-Host ("  [{0}] {1}: ok={2} fail={3} max={4}ms" -f $mark, $s.label, $s.ok, $s.fail, $s.max_ms) -ForegroundColor $color
        }
        $g = $stress.growth
        $gmark = if ($g.pass) { 'PASS' } else { 'FAIL' }
        $gcolor = if ($g.pass) { 'Green' } else { 'Red' }
        Write-Host ("  [{0}] growth feige+{1} python+{2} node+{3}" -f $gmark, $g.feige_delta, $g.python_delta, $g.node_delta) -ForegroundColor $gcolor
    }
    Write-Host ''
    if ($report.ok) {
        Write-Host 'OVERALL: PASS' -ForegroundColor Green
    }
    else {
        Write-Host 'OVERALL: FAIL' -ForegroundColor Red
    }
}

if (-not $report.ok) { exit 1 }
exit 0
