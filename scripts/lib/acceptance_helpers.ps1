# Shared helpers for acceptance / stress scripts (non-blocking EXE start + health gates).
$script:AcceptanceRoot = if ($PSScriptRoot) {
    Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
} else {
    Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$script:AcceptanceProjectPattern = 'douyin-pigeon-protocol|pigeon-feige|run\.py|go-bridge|pigeon_protocol|run_bdms_daemon\.mjs|run_bdms_fetch\.mjs'

function Get-AcceptanceProjectCounts {
    $filter = "Name='node.exe' OR Name='python.exe' OR Name='python3.exe' OR Name='pigeon-feige.exe'"
    $rows = @(Get-CimInstance Win32_Process -Filter $filter -OperationTimeoutSec 15 -ErrorAction SilentlyContinue)
    $feige = 0; $py = 0; $node = 0
    foreach ($r in $rows) {
        $cmd = [string]$r.CommandLine
        $name = [string]$r.Name
        if ($name -ieq 'pigeon-feige.exe') { $feige++; continue }
        if (-not ($cmd -match $script:AcceptanceProjectPattern)) { continue }
        if ($name -ieq 'node.exe') { $node++ }
        elseif ($name -match '^python') { $py++ }
    }
    return @{ feige = $feige; python = $py; node = $node }
}

function Get-AcceptancePort8765Pids {
    $pids = @()
    try {
        $rows = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
        foreach ($row in $rows) {
            if ($row.OwningProcess -and $row.OwningProcess -ne 0) {
                $pids += [int]$row.OwningProcess
            }
        }
    }
    catch {
        $matches = netstat -ano | Select-String ':8765\s+.*LISTENING'
        foreach ($m in $matches) {
            $parts = ($m.ToString().Trim() -split '\s+')
            if ($parts.Count -ge 5) { $pids += [int]$parts[-1] }
        }
    }
    return @($pids | Sort-Object -Unique)
}

function Stop-AcceptanceProjectProcesses {
    taskkill /F /IM pigeon-feige.exe 2>$null | Out-Null

    Get-CimInstance Win32_Process -Filter "name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'douyin-pigeon-protocol|go-bridge|pigeon_protocol|run\.py' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    Get-CimInstance Win32_Process -Filter "name = 'node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'douyin-pigeon-protocol|run_bdms_daemon\.mjs|run_bdms_fetch\.mjs|pigeon_protocol|pigeon-feige' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

    foreach ($pid in (Get-AcceptancePort8765Pids)) {
        Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

function Start-AcceptanceExe {
    param(
        [string]$Root = $script:AcceptanceRoot
    )
    $exe = Join-Path $Root 'dist\pigeon-feige.exe'
    if (-not (Test-Path $exe)) {
        throw "missing EXE: $exe"
    }
    $env:PIGEON_HEADLESS = '1'
    $env:PIGEON_PROJECT_ROOT = $Root
    $env:PIGEON_ROOT = $Root
    Start-Process -FilePath $exe -WorkingDirectory $Root | Out-Null
}

function Test-AcceptanceApiHealth {
    param(
        [string]$BaseUrl = 'http://127.0.0.1:8765',
        [int]$TimeoutSec = 2
    )
    try {
        $raw = curl.exe -sS -m $TimeoutSec -w "`n%{http_code}" ($BaseUrl.TrimEnd('/') + '/api/health') 2>$null
        if (-not $raw) { return $false }
        $lines = @($raw -split "`n")
        if ($lines.Count -lt 2) { return $false }
        $code = [int]$lines[-1]
        return ($code -ge 200 -and $code -lt 300)
    }
    catch {
        return $false
    }
}

function Wait-AcceptanceApiHealth {
    param(
        [string]$BaseUrl = 'http://127.0.0.1:8765',
        [int]$MaxAttempts = 40,
        [int]$SleepMs = 500
    )
    for ($i = 0; $i -lt $MaxAttempts; $i++) {
        if (Test-AcceptanceApiHealth -BaseUrl $BaseUrl) {
            return $true
        }
        Start-Sleep -Milliseconds $SleepMs
    }
    return $false
}

function Get-AcceptanceHealthJson {
    param(
        [string]$BaseUrl = 'http://127.0.0.1:8765',
        [int]$TimeoutSec = 3
    )
    try {
        $resp = Invoke-WebRequest -Uri ($BaseUrl.TrimEnd('/') + '/api/health') -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        if ($resp.StatusCode -lt 200 -or $resp.StatusCode -ge 300) { return $null }
        return ($resp.Content | ConvertFrom-Json -ErrorAction Stop)
    }
    catch {
        return $null
    }
}

function Test-AcceptanceBridgeReady {
    param(
        [string]$BaseUrl = 'http://127.0.0.1:8765'
    )
    $h = Get-AcceptanceHealthJson -BaseUrl $BaseUrl
    if (-not $h) { return $false }
    if ($h.bridge_ready -eq $true) { return $true }
    if ($h.go_api_ok -eq $true -and $h.python_daemon_live -eq $true) { return $true }
    return $false
}

function Wait-AcceptanceBridgeReady {
    param(
        [string]$BaseUrl = 'http://127.0.0.1:8765',
        [int]$MaxAttempts = 30,
        [int]$SleepMs = 500
    )
    for ($i = 0; $i -lt $MaxAttempts; $i++) {
        if (Test-AcceptanceBridgeReady -BaseUrl $BaseUrl) {
            return $true
        }
        Start-Sleep -Milliseconds $SleepMs
    }
    return $false
}

function Test-AcceptanceFailFast {
    param(
        [string]$BaseUrl = 'http://127.0.0.1:8765',
        [string]$Hint = ''
    )
    if (-not (Test-AcceptanceApiHealth -BaseUrl $BaseUrl)) {
        Write-AcceptanceRecoveryDiagnostics -Hint ($Hint + ' API health down')
        return $false
    }
    $counts = Get-AcceptanceProjectCounts
    if ($counts.feige -ne 1) {
        Write-AcceptanceRecoveryDiagnostics -Hint ($Hint + " feige=$($counts.feige)")
        return $false
    }
    return $true
}

function Write-AcceptanceScriptFinal {
    param(
        [string]$Label,
        [int]$ExitCode
    )
    $val = if ($ExitCode -eq 0) { 0 } else { 1 }
    Write-Host ''
    Write-Host 'FINAL:'
    Write-Host ("  {0}={1}" -f $Label, $val)
    if ($val -eq 0) {
        Write-Host 'OVERALL: PASS' -ForegroundColor Green
    }
    else {
        Write-Host 'OVERALL: FAIL' -ForegroundColor Red
    }
    return $val
}

function Write-AcceptanceFullFinal {
    param(
        [hashtable]$Results
    )
    $fail = $false
    Write-Host ''
    Write-Host 'FINAL:'
    foreach ($key in @('context_orders', 'stability', 'bridge', 'gui_smoke', 'gui_close', 'pure')) {
        $v = 0
        if ($Results.ContainsKey($key)) {
            $v = if ([int]$Results[$key] -eq 0) { 0 } else { 1 }
        }
        Write-Host ("  {0}={1}" -f $key, $v)
        if ($v -ne 0) { $fail = $true }
    }
    if ($fail) {
        Write-Host 'OVERALL: FAIL' -ForegroundColor Red
        return 1
    }
    Write-Host 'OVERALL: PASS' -ForegroundColor Green
    return 0
}

function Write-AcceptanceFeigeExitDiagnostics {
    param(
        [string]$Root = $script:AcceptanceRoot
    )
    Write-Host ''
    Write-Host '=== Feige exit diagnostics ===' -ForegroundColor Red
    try {
        Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'Application Error'; StartTime = (Get-Date).AddHours(-1) } -MaxEvents 5 -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host $_.Message }
    }
    catch {}
    $counts = Get-AcceptanceProjectCounts
    Write-Host ("  feige={0} python={1} node={2}" -f $counts.feige, $counts.python, $counts.node)
    Write-Host ("  8765 pids: {0}" -f (Get-AcceptancePort8765Pids -join ', '))
    $logDir = Join-Path $Root 'logs\runtime'
    if (Test-Path $logDir) {
        Write-Host '  recent logs/runtime (20):'
        Get-ChildItem $logDir -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 20 |
            ForEach-Object { Write-Host ("    {0} ({1})" -f $_.Name, $_.LastWriteTime) }
    }
}


function Wait-AcceptanceDaemonReady {
    param(
        [int]$MaxAttempts = 30,
        [int]$SleepMs = 500
    )
    for ($i = 0; $i -lt $MaxAttempts; $i++) {
        $counts = Get-AcceptanceProjectCounts
        if ($counts.feige -eq 1 -and $counts.python -ge 1) {
            return $true
        }
        Start-Sleep -Milliseconds $SleepMs
    }
    return $false
}

function Write-AcceptanceRecoveryDiagnostics {
    param(
        [string]$Root = $script:AcceptanceRoot,
        [string]$Hint = ''
    )
    Write-Host '' -ForegroundColor Red
    Write-Host '=== Recovery diagnostics ===' -ForegroundColor Red
    if ($Hint) { Write-Host $Hint -ForegroundColor Yellow }
    $counts = Get-AcceptanceProjectCounts
    $portPids = Get-AcceptancePort8765Pids
    Write-Host ("  feige={0} python={1} node={2}" -f $counts.feige, $counts.python, $counts.node)
    Write-Host ("  8765 listen pids: {0}" -f ($portPids -join ', '))
    Write-Host ("  health ready: {0}" -f (Test-AcceptanceApiHealth))
    $logDir = Join-Path $Root 'logs\runtime'
    if (Test-Path $logDir) {
        Write-Host '  recent logs/runtime:'
        Get-ChildItem $logDir -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 5 |
            ForEach-Object { Write-Host ("    {0} ({1})" -f $_.Name, $_.LastWriteTime) }
    }
    Write-Host ''
    Write-Host 'Recovery:' -ForegroundColor Yellow
    Write-Host '  cd D:\douyin-pigeon-protocol'
    Write-Host '  Stop-AcceptanceProjectProcesses  # or taskkill pigeon-feige.exe'
    Write-Host '  Start-Process -FilePath .\dist\pigeon-feige.exe -WorkingDirectory D:\douyin-pigeon-protocol'
    Write-Host '  Wait until curl http://127.0.0.1:8765/api/health returns ok'
}

function Assert-AcceptanceServiceReady {
    param(
        [string]$BaseUrl = 'http://127.0.0.1:8765',
        [switch]$AllowRestart
    )
    if (-not (Test-AcceptanceApiHealth -BaseUrl $BaseUrl)) {
        if ($AllowRestart) {
            Stop-AcceptanceProjectProcesses
            Start-AcceptanceExe
            if (-not (Wait-AcceptanceApiHealth -BaseUrl $BaseUrl)) {
                Write-AcceptanceRecoveryDiagnostics -Hint 'API not ready after restart'
                return $false
            }
        }
        else {
            Write-AcceptanceRecoveryDiagnostics -Hint 'API not ready — start EXE before acceptance'
            return $false
        }
    }
    $counts = Get-AcceptanceProjectCounts
    if ($counts.feige -ne 1) {
        Write-AcceptanceRecoveryDiagnostics -Hint ("expected feige=1 before stress, got feige={0}" -f $counts.feige)
        return $false
    }
    return $true
}

function Invoke-AcceptanceHotPath {
    param(
        [string]$BaseUrl,
        [string]$Path,
        [int]$TimeoutSec = 12
    )
    $url = ($BaseUrl.TrimEnd('/')) + $Path
    $started = Get-Date
    $raw = curl.exe -sS -m $TimeoutSec -w "`n%{http_code}" $url 2>&1
    $ms = [int]((Get-Date) - $started).TotalMilliseconds
    $lines = @($raw -split "`n")
    $code = if ($lines.Count -ge 2) { [int]$lines[-1] } else { 0 }
    return @{ ms = $ms; code = $code; ok = ($code -ge 200 -and $code -lt 300) }
}

function Initialize-GuiCloseWin32 {
    if (-not ("Win32.NativeMethods" -as [type])) {
        Add-Type -Namespace Win32 -Name NativeMethods -MemberDefinition @'
[DllImport("user32.dll", CharSet=CharSet.Auto)]
public static extern bool PostMessage(System.IntPtr hWnd, uint Msg, System.IntPtr wParam, System.IntPtr lParam);
[DllImport("user32.dll", CharSet=CharSet.Auto)]
public static extern IntPtr SendMessage(System.IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
'@
    }
}

function Request-GuiClose {
    Initialize-GuiCloseWin32
    $procs = @(Get-Process pigeon-feige -ErrorAction SilentlyContinue)
    if ($procs.Count -eq 0) { return $false }
    $WM_CLOSE = 0x0010
    foreach ($p in $procs) {
        try {
            if ($p.MainWindowHandle -ne [IntPtr]::Zero) {
                [void][Win32.NativeMethods]::PostMessage($p.MainWindowHandle, $WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero)
                [void]$p.CloseMainWindow()
            }
            else {
                [void]$p.CloseMainWindow()
            }
        }
        catch {}
    }
    return $true
}

function Test-Port8765Released {
    $pids = Get-AcceptancePort8765Pids
    return ($pids.Count -eq 0)
}

function Wait-GuiGracefulExit {
    param(
        [int]$CloseWaitSec = 15,
        [int]$Retries = 4
    )
    $requested = Request-GuiClose
    if (-not $requested) {
        return @{ ok = $true; graceful = $true; message = 'already exited' }
    }
    for ($try = 0; $try -lt $Retries; $try++) {
        for ($i = 0; $i -lt $CloseWaitSec; $i++) {
            $feige = @(Get-Process pigeon-feige -ErrorAction SilentlyContinue).Count
            if ($feige -eq 0 -and (Test-Port8765Released)) {
                return @{ ok = $true; graceful = $true; message = 'exe and port released' }
            }
            Start-Sleep -Seconds 1
        }
        if ($try -lt ($Retries - 1)) {
            Request-GuiClose | Out-Null
        }
    }
    $feigeLeft = @(Get-Process pigeon-feige -ErrorAction SilentlyContinue).Count
    $portPids = Get-AcceptancePort8765Pids
    return @{
        ok        = ($feigeLeft -eq 0 -and $portPids.Count -eq 0)
        graceful  = $false
        message   = "feige=$feigeLeft port=$($portPids -join ',')"
    }
}
