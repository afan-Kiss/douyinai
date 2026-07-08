# 将本机开发环境从 C 盘迁到 E 盘（Go + GOPATH）
# 用法: .\scripts\migrate_dev_to_e.ps1 [-WhatIf]
param([switch]$WhatIf)

$ErrorActionPreference = "Stop"
$DevRoot = "E:\devtools"
$GoDest = Join-Path $DevRoot "go"
$GoBin = Join-Path $GoDest "bin\go.exe"
$GoPathDest = Join-Path $DevRoot "gopath"
$OldSystemGo = "C:\Go"
$OldGoPath = Join-Path $env:USERPROFILE "go"

function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

function Update-UserPath {
    param([string[]]$Add, [string[]]$Remove)
    $cur = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($cur -split ";" | Where-Object { $_ -and ($Remove -notcontains $_) })
    foreach ($p in $Add) {
        if ($p -and ($parts -notcontains $p)) { $parts = @($p) + $parts }
    }
    $newPath = ($parts | Select-Object -Unique) -join ";"
    if (-not $WhatIf) {
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    }
    Write-Host "User PATH updated (restart terminal / Cursor to生效)"
}

Write-Step "目标目录: $DevRoot"
if (-not $WhatIf) {
    New-Item -ItemType Directory -Force -Path $DevRoot | Out-Null
}

# 1) Go: C:\Go -> E:\devtools\go
if (Test-Path $OldSystemGo) {
    if (Test-Path $GoDest) {
        Write-Host "Skip Go copy: $GoDest already exists"
    } else {
        Write-Step "Copy Go $OldSystemGo -> $GoDest"
        if ($WhatIf) {
            Write-Host "[WhatIf] robocopy $OldSystemGo $GoDest /E"
        } else {
            robocopy $OldSystemGo $GoDest /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
            if (-not (Test-Path $GoBin)) { throw "Go migrate failed: $GoBin missing" }
        }
    }
} elseif (Test-Path $GoBin) {
    Write-Host "Go already at $GoDest"
} else {
    Write-Host "No C:\Go found; run scripts\install_go.ps1 to install to E:\devtools\go"
}

# 2) GOPATH: %USERPROFILE%\go -> E:\devtools\gopath
if (Test-Path $OldGoPath) {
    if (Test-Path $GoPathDest) {
        Write-Host "Skip GOPATH: $GoPathDest exists"
    } else {
        Write-Step "Move GOPATH $OldGoPath -> $GoPathDest"
        if ($WhatIf) {
            Write-Host "[WhatIf] robocopy $OldGoPath $GoPathDest /E"
        } else {
            New-Item -ItemType Directory -Force -Path $GoPathDest | Out-Null
            robocopy $OldGoPath $GoPathDest /E /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
        }
    }
} elseif (-not (Test-Path $GoPathDest)) {
    if (-not $WhatIf) { New-Item -ItemType Directory -Force -Path $GoPathDest | Out-Null }
}

# 3) 环境变量
$goBinDir = Join-Path $GoDest "bin"
$goPathBin = Join-Path $GoPathDest "bin"
if (-not $WhatIf) {
    if (Test-Path $GoBin) {
        [Environment]::SetEnvironmentVariable("GOROOT", $GoDest, "User")
    }
    [Environment]::SetEnvironmentVariable("GOPATH", $GoPathDest, "User")
}
Update-UserPath -Add @($goBinDir, $goPathBin) -Remove @(
    "C:\Go\bin",
    (Join-Path $OldGoPath "bin")
)

# 4) 验证
if ((Test-Path $GoBin) -and -not $WhatIf) {
    Write-Step "Verify"
    & $GoBin version
    & $GoBin env GOROOT GOPATH
    Write-Host "`nOptional: after confirming builds work, remove old C:\Go to free ~250MB:"
    Write-Host "  Remove-Item -Recurse -Force C:\Go"
}

Write-Step "Done"
Write-Host "Layout: E:\devtools\go (Go), E:\devtools\gopath, E:\Python312, E:\nodejs"
Write-Host "Project: D:\douyin-pigeon-protocol — copy whole folder to E:\ if needed"
