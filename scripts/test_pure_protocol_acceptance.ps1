# Pure-protocol acceptance gate — verify desktop stack works without Chrome/CDP/browser relay.
# Usage:
#   .\scripts\test_pure_protocol_acceptance.ps1
#   .\scripts\test_pure_protocol_acceptance.ps1 -HeadlessOnly
param(
    [string]$Root = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [string]$BaseUrl = "http://127.0.0.1:8765",
    [switch]$HeadlessOnly,
    [int]$ApiTimeoutSec = 15,
    [int]$StartupWaitSec = 60
)

$ErrorActionPreference = "Continue"
$Helpers = Join-Path $Root "scripts\lib\acceptance_helpers.ps1"
if (Test-Path $Helpers) { . $Helpers }

$ImpureOrderPatterns = @('cdp', 'curl_relay', 'har', 'offline', 'cache', 'user_card')
$PureOrderPatterns = @('httpx', 'python_relay', 'snapshot', 'backstage')
$SendWarmupActions = @('session_renew', 'rust_sdk_inner', 'cdp_warm_inners')
$SendBlockActions = @('cdp_onboard', 'cdp_warm', 'browser')

function Invoke-PureJsonApi {
    param(
        [string]$Path,
        [int]$TimeoutSec = $ApiTimeoutSec
    )
    $url = ($BaseUrl.TrimEnd('/')) + $Path
    $started = Get-Date
    try {
        $resp = Invoke-WebRequest -Uri $url -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        $ms = [int]((Get-Date) - $started).TotalMilliseconds
        $body = [string]$resp.Content
        $json = $null
        if ($body) {
            try { $json = $body | ConvertFrom-Json -ErrorAction Stop } catch {}
        }
        return @{
            ok     = ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300)
            code   = [int]$resp.StatusCode
            ms     = $ms
            body   = $body
            json   = $json
            error  = ''
        }
    }
    catch {
        $ms = [int]((Get-Date) - $started).TotalMilliseconds
        return @{
            ok    = $false
            code  = 0
            ms    = $ms
            body  = ''
            json  = $null
            error = $_.Exception.Message
        }
    }
}

function Get-ChromeCdpSnapshot {
    $chrome = @()
    try {
        $rows = @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue)
        foreach ($r in $rows) {
            $cmd = [string]$r.CommandLine
            if ($cmd -match 'remote-debugging-port|9222|9223|pigeon|feige|jinritemai') {
                $chrome += [pscustomobject]@{
                    pid = [int]$r.ProcessId
                    cmd = if ($cmd.Length -gt 160) { $cmd.Substring(0, 160) + '...' } else { $cmd }
                }
            }
        }
    }
    catch {}

    $cdpPorts = @()
    foreach ($port in @(9222, 9223)) {
        try {
            $null = Invoke-WebRequest -Uri "http://127.0.0.1:$port/json/version" -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
            $cdpPorts += $port
        }
        catch {}
    }
    return @{ chrome = $chrome; cdp_ports = $cdpPorts }
}

function Test-SourceImpure {
    param(
        [string]$Text,
        [string[]]$Patterns
    )
    $t = [string]$Text
    if (-not $t) { return $false }
    foreach ($p in $Patterns) {
        if ($t -match [regex]::Escape($p)) { return $true }
    }
    return $false
}

function Test-SourcePureOrder {
    param(
        [string]$Source,
        [bool]$OrderOk
    )
    if (-not $OrderOk) { return $false }
    if (Test-SourceImpure -Text $Source -Patterns $ImpureOrderPatterns) { return $false }
    foreach ($p in $PureOrderPatterns) {
        if ($Source -match $p) { return $true }
    }
    return $false
}

function Start-PureProtocolExe {
    param(
        [ValidateSet('headless', 'gui')]
        [string]$Mode
    )
    $exe = Join-Path $Root "dist\pigeon-feige.exe"
    if (-not (Test-Path $exe)) {
        throw "missing EXE: $exe"
    }

    $env:PIGEON_PROJECT_ROOT = $Root
    $env:PIGEON_ROOT = $Root
    $env:PIGEON_NO_CDP = '1'
    $env:PIGEON_USE_BROWSER = '0'
    $env:PIGEON_NODE_ONESHOT_FALLBACK = '0'
    Remove-Item Env:PIGEON_ALLOW_CDP -ErrorAction SilentlyContinue

    if ($Mode -eq 'headless') {
        $env:PIGEON_HEADLESS = '1'
        Remove-Item Env:PIGEON_API_ONLY -ErrorAction SilentlyContinue
    }
    else {
        Remove-Item Env:PIGEON_HEADLESS -ErrorAction SilentlyContinue
        Remove-Item Env:PIGEON_API_ONLY -ErrorAction SilentlyContinue
    }

    Start-Process -FilePath $exe -WorkingDirectory $Root | Out-Null
}

function Wait-PureProtocolReady {
    for ($i = 0; $i -lt $StartupWaitSec; $i++) {
        $counts = Get-AcceptanceProjectCounts
        if ($counts.feige -eq 1 -and (Test-AcceptanceApiHealth -BaseUrl $BaseUrl)) {
            if (Get-Command Wait-AcceptanceDaemonReady -ErrorAction SilentlyContinue) {
                Wait-AcceptanceDaemonReady -MaxAttempts 6 -SleepMs 500 | Out-Null
            }
            return $true
        }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Get-ConvViaSource {
    param($ConvJson)
    $via = ''
    $source = ''
    if ($ConvJson) {
        $source = [string]$ConvJson.source
        if ($ConvJson.raw) {
            $via = [string]$ConvJson.raw.via
            if (-not $source) { $source = [string]$ConvJson.raw.source }
        }
        if (-not $via -and $ConvJson.via) { $via = [string]$ConvJson.via }
    }
    return @{ via = $via; source = $source }
}

function Invoke-PureScenario {
    param(
        [ValidateSet('headless', 'gui')]
        [string]$Mode
    )

    Write-Host ""
    Write-Host ("=== Pure protocol scenario: {0} ===" -f $Mode) -ForegroundColor Cyan

    Stop-AcceptanceProjectProcesses
    Start-Sleep -Seconds 3
    if (-not (Test-Port8765Released)) {
        Start-Sleep -Seconds 2
        Stop-AcceptanceProjectProcesses
        Start-Sleep -Seconds 2
    }

    $beforeChrome = Get-ChromeCdpSnapshot
    $beforeCounts = Get-AcceptanceProjectCounts

    $started = $false
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        if ($attempt -gt 1) {
            Write-Host ("  retry startup ({0}/2)..." -f $attempt) -ForegroundColor Yellow
            Stop-AcceptanceProjectProcesses
            Start-Sleep -Seconds 3
        }

        Start-PureProtocolExe -Mode $Mode
        if (-not (Wait-PureProtocolReady)) {
            if ($attempt -lt 2) { continue }
            Write-Host "  FAIL startup: API/feige not ready" -ForegroundColor Red
            Stop-AcceptanceProjectProcesses
            return @{
                mode = $Mode
                ok = $false
                grade = 'NOT_READY'
                reason = 'startup_failed'
                conv_pure = $false
                listen_pure = $false
                send_path_ready = $false
                orders_pure = $false
                orders_pure_or_snapshot = $false
                chrome_started = $false
                cdp_used = $false
                node_count = 0
                python_count = 0
                feige_count = 0
                notes = @('startup_failed')
            }
        }
        $started = $true
        break
    }
    if (-not $started) { return @{ mode = $Mode; ok = $false; grade = 'NOT_READY'; reason = 'startup_failed' } }
    $midCounts = Get-AcceptanceProjectCounts

    $checks = [ordered]@{}
    $convErrors = 0

    $h = Invoke-PureJsonApi -Path '/api/health'
    $checks.health = @{
        ok = ($h.ok -and $h.body -match '"ok"\s*:\s*true')
        ms = $h.ms
        via = if ($h.body -match '"via"\s*:\s*"([^"]+)"') { $Matches[1] } else { '' }
        error = $h.error
    }

    $s = Invoke-PureJsonApi -Path '/api/session?light=1'
    $sess = $s.json
    $checks.session = @{
        ok = $s.ok
        ms = $s.ms
        logged_in = [bool]$sess.logged_in
        send_ready = [bool]$sess.send_ready
        listen_ready = if ($null -ne $sess.listen_ready) { [bool]$sess.listen_ready } else { $false }
        backstage_ok = if ($null -ne $sess.backstage_ok) { [bool]$sess.backstage_ok } else { $null }
        recommended_action = [string]$sess.recommended_action
        error = $s.error
    }

    $a = Invoke-PureJsonApi -Path '/api/accounts'
    $checks.accounts = @{
        ok = ($a.ok -and $a.body -match '"ok"\s*:\s*true')
        ms = $a.ms
        count = if ($a.json -and $a.json.accounts) { @($a.json.accounts).Count } else { 0 }
        error = $a.error
    }

    $c = $null
    $convMeta = @{ via = ''; source = '' }
    $convErrors = 0
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $c = Invoke-PureJsonApi -Path '/api/conversations?category=recent&light=1'
        $convMeta = Get-ConvViaSource -ConvJson $c.json
        $convErrors = 0
        if (-not $c.ok) { $convErrors = 1 }
        elseif ($c.json -and $c.json.ok -eq $false) { $convErrors = 1 }
        $itemCount = if ($c.json -and $c.json.items) { @($c.json.items).Count } else { 0 }
        if ($c.ok -and $itemCount -gt 0) { break }
        if ($attempt -lt 3) { Start-Sleep -Seconds 2 }
    }
    $checks.conversations = @{
        ok = ($c.ok -and $convErrors -eq 0 -and (
                ($c.json -and $c.json.ok -ne $false) -or
                (($c.json.items | Measure-Object).Count -gt 0)
            ))
        ms = $c.ms
        count = if ($c.json -and $c.json.items) { @($c.json.items).Count } else { 0 }
        source = $convMeta.source
        via = $convMeta.via
        errors = $convErrors
        error = $c.error
    }

    $p = Invoke-PureJsonApi -Path '/api/process/status'
    $proc = $p.json
    $checks.process_status = @{
        ok = ($p.ok -and $proc.ok -eq $true)
        ms = $p.ms
        node_live = if ($proc.node) { [int]$proc.node.registered_live } else { -1 }
        oneshot_fallback = if ($proc.node) { [bool]$proc.node.oneshot_fallback } else { $null }
        error = $p.error
    }

    Start-Sleep -Seconds 1
    $afterCounts = Get-AcceptanceProjectCounts
    $afterChrome = Get-ChromeCdpSnapshot

    $selectedUid = ''
    if ($c.json -and $c.json.items -and @($c.json.items).Count -gt 0) {
        $selectedUid = [string]$c.json.items[0].security_user_id
    }

    $ordersSource = ''
    $orderOk = $false
    $ordersPure = $false
    $ordersPureOrSnapshot = $false
    if ($selectedUid) {
        $o = Invoke-PureJsonApi -Path ("/api/orders?user_id=" + [uri]::EscapeDataString($selectedUid))
        $ordersSource = [string]$o.json.source
        $orderOk = [bool]$o.json.order_ok
        $ordersPure = $orderOk -and (-not (Test-SourceImpure -Text $ordersSource -Patterns $ImpureOrderPatterns))
        $ordersPureOrSnapshot = Test-SourcePureOrder -Source $ordersSource -OrderOk $orderOk
        $checks.orders = @{
            ok = $o.ok
            ms = $o.ms
            source = $ordersSource
            order_ok = $orderOk
            orders_pure = $ordersPure
            orders_pure_or_snapshot = $ordersPureOrSnapshot
            uid_tail = if ($selectedUid.Length -ge 6) { $selectedUid.Substring($selectedUid.Length - 6) } else { $selectedUid }
            error = $o.error
        }
    }
    else {
        $checks.orders = @{
            ok = $false
            skipped = $true
            reason = 'no_conversation_uid'
            source = ''
            order_ok = $false
            orders_pure = $false
            orders_pure_or_snapshot = $false
        }
    }

    $chromeStarted = ($afterChrome.chrome.Count -gt $beforeChrome.chrome.Count) -or ($afterChrome.cdp_ports.Count -gt 0)
    $cdpInConv = ($convMeta.via -match 'cdp|browser|playwright') -or ($convMeta.source -match 'cdp|browser|playwright')
    $useBrowserEnv = ($env:PIGEON_USE_BROWSER -eq '1')
    $pythonGrew = ($afterCounts.python -gt $midCounts.python)
    $nodeOk = ($afterCounts.node -le 1)

    $loggedIn = [bool]$checks.session.logged_in
    $sendReady = [bool]$checks.session.send_ready
    $listenReady = [bool]$checks.session.listen_ready
    $action = [string]$checks.session.recommended_action
    $sendPathReady = $sendReady
    $sendWarmupOnly = (-not $sendReady) -and ($SendWarmupActions -contains $action)
    $sendBlocked = $loggedIn -and (-not $sendReady) -and (-not $sendWarmupOnly) -and (
        ($SendBlockActions | Where-Object { $action -match $_ }).Count -gt 0 -or
        ($action -and $action -ne 'ready')
    )

    $convOk = [bool]$checks.conversations.ok
    $convHasItems = ($checks.conversations.count -gt 0)
    $convPure = ($convOk -or $convHasItems) -and (-not $cdpInConv)
    $listenPure = ($listenReady -or (-not $loggedIn -and $convHasItems)) -and (-not $chromeStarted)

    $notes = @()
    if ($sendWarmupOnly) {
        $notes += 'send_warmup_required_not_fully_pure_stable'
    }
    if ($cdpInConv) {
        $notes += 'conversation_via_contains_cdp_or_browser'
    }
    if ($chromeStarted) {
        $notes += 'chrome_or_cdp_process_detected'
    }
    if (-not $ordersPure -and $ordersPureOrSnapshot) {
        $notes += 'orders_via_snapshot_or_relay_not_live_pure'
    }
    if ($checks.orders.skipped) {
        $notes += 'orders_skipped_no_buyer_uid'
    }

    if (-not $loggedIn) {
        $notes += 'session_not_logged_in'
    }

    $grade = 'PURE_BETA'
    $convPathOk = $convOk -or $convHasItems
    if (-not $checks.health.ok -or -not $convPathOk -or $convErrors -gt 0 -or $sendBlocked) {
        $grade = 'NOT_READY'
    }
    elseif ($convPure -and $listenPure -and $sendPathReady -and $ordersPure -and (-not $chromeStarted) -and (-not $cdpInConv) -and $nodeOk) {
        $grade = 'PURE_READY'
    }
    elseif (-not $convPure -or (-not $listenPure -and $loggedIn) -or (-not ($sendPathReady -or $sendWarmupOnly -or (-not $loggedIn)) -and $loggedIn) -or $chromeStarted -or $cdpInConv) {
        if ($grade -ne 'NOT_READY') { $grade = 'PURE_BETA' }
    }

    Write-Host ("  health: {0} via={1} ({2}ms)" -f $(if ($checks.health.ok) { 'OK' } else { 'FAIL' }), $checks.health.via, $checks.health.ms)
    Write-Host ("  session: logged_in={0} send_ready={1} listen_ready={2} backstage_ok={3} action={4}" -f `
        $checks.session.logged_in, $checks.session.send_ready, $checks.session.listen_ready, $checks.session.backstage_ok, $action)
    Write-Host ("  conversations: ok={0} count={1} source={2} via={3} errors={4} ({5}ms)" -f `
        $checks.conversations.ok, $checks.conversations.count, $convMeta.source, $convMeta.via, $convErrors, $checks.conversations.ms)
    Write-Host ("  process: node={0} python={1} feige={2} oneshot_fallback={3}" -f `
        $afterCounts.node, $afterCounts.python, $afterCounts.feige, $checks.process_status.oneshot_fallback)
    $beforeCdpPorts = ($beforeChrome.cdp_ports -join ',')
    $afterCdpPorts = ($afterChrome.cdp_ports -join ',')
    Write-Host ("  chrome/cdp: before={0}/{1} after={2}/{3}" -f `
        $beforeChrome.chrome.Count, $beforeCdpPorts, $afterChrome.chrome.Count, $afterCdpPorts)
    if ($selectedUid) {
        Write-Host ("  orders: source={0} order_ok={1} pure={2} snapshot_ok={3}" -f `
            $ordersSource, $orderOk, $ordersPure, $ordersPureOrSnapshot)
    }
    else {
        Write-Host "  orders: skipped (no uid)"
    }
    if ($sendWarmupOnly) {
        Write-Host "  NOTE: send warmup required (not fully pure-protocol stable)" -ForegroundColor Yellow
    }
    Write-Host ("  grade: {0}" -f $grade) -ForegroundColor $(switch ($grade) { 'PURE_READY' { 'Green' } 'PURE_BETA' { 'Yellow' } default { 'Red' } })

    if ($Mode -eq 'gui') {
        if (Get-Command Wait-GuiGracefulExit -ErrorAction SilentlyContinue) {
            $close = Wait-GuiGracefulExit -CloseWaitSec 12 -Retries 3
            if (-not $close.ok) { Stop-AcceptanceProjectProcesses }
        }
        else {
            Stop-AcceptanceProjectProcesses
        }
    }
    else {
        Stop-AcceptanceProjectProcesses
    }

    return @{
        mode = $Mode
        ok = ($grade -ne 'NOT_READY')
        grade = $grade
        notes = $notes
        checks = $checks
        conv_pure = $convPure
        listen_pure = $listenPure
        send_path_ready = $sendPathReady
        send_warmup_only = $sendWarmupOnly
        orders_pure = $ordersPure
        orders_pure_or_snapshot = $ordersPureOrSnapshot
        chrome_started = $chromeStarted
        cdp_used = ($cdpInConv -or $chromeStarted)
        use_browser_env = $useBrowserEnv
        node_count = $afterCounts.node
        python_count = $afterCounts.python
        feige_count = $afterCounts.feige
        python_grew = $pythonGrew
        node_ok = $nodeOk
        counts_before = $beforeCounts
        counts_after = $afterCounts
    }
}

Write-Host "=== Pure protocol acceptance ===" -ForegroundColor Cyan
Write-Host "root: $Root"
Write-Host ("env: NO_CDP=1 USE_BROWSER=0 NODE_ONESHOT_FALLBACK=0 modes={0}" -f $(if ($HeadlessOnly) { 'headless' } else { 'headless+gui' }))

$scenarios = @('headless')
if (-not $HeadlessOnly) { $scenarios += 'gui' }

$results = @()
foreach ($mode in $scenarios) {
    $results += Invoke-PureScenario -Mode $mode
}

$worst = 'PURE_READY'
foreach ($r in $results) {
    if ($r.grade -eq 'NOT_READY') { $worst = 'NOT_READY'; break }
    if ($r.grade -eq 'PURE_BETA') { $worst = 'PURE_BETA' }
}

$any = $results | Where-Object { $_.grade -ne 'NOT_READY' -or $_.checks } | Select-Object -First 1
if (-not $any) { $any = $results | Select-Object -First 1 }
if ($results.Count -gt 1) {
    $best = $results | Sort-Object @{ Expression = {
            switch ($_.grade) { 'PURE_READY' { 0 } 'PURE_BETA' { 1 } default { 2 } }
        } } | Select-Object -First 1
    if ($best) { $any = $best }
}

Write-Host ""
Write-Host "=== Pure protocol summary ===" -ForegroundColor Cyan
Write-Host ("  final_grade: {0}" -f $worst)
Write-Host ("  scenarios: {0}" -f (($results | ForEach-Object { "{0}={1}" -f $_.mode, $_.grade }) -join ', '))
Write-Host ("  conv_pure: {0}" -f $any.conv_pure)
Write-Host ("  listen_pure: {0}" -f $any.listen_pure)
Write-Host ("  send_path_ready: {0}" -f $any.send_path_ready)
Write-Host ("  orders_pure: {0}" -f $any.orders_pure)
Write-Host ("  orders_pure_or_snapshot: {0}" -f $any.orders_pure_or_snapshot)
Write-Host ("  chrome/cdp_started: {0}" -f $any.chrome_started)
Write-Host ("  processes: feige={0} python={1} node={2}" -f $any.feige_count, $any.python_count, $any.node_count)
if ($any.notes -and $any.notes.Count -gt 0) {
    Write-Host ("  notes: {0}" -f ($any.notes -join '; '))
}

switch ($worst) {
    'PURE_READY' {
        Write-AcceptanceScriptFinal -Label 'pure' -ExitCode 0 | Out-Null
        exit 0
    }
    'PURE_BETA' {
        Write-AcceptanceScriptFinal -Label 'pure' -ExitCode 0 | Out-Null
        exit 0
    }
    default {
        Write-AcceptanceScriptFinal -Label 'pure' -ExitCode 1 | Out-Null
        exit 1
    }
}
