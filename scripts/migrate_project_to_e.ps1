# 将整个 pigeon-protocol 项目迁到 E 盘
param(
    [string]$Dest = "E:\douyin-pigeon-protocol",
    [string]$Source = "D:\douyin-pigeon-protocol",
    [switch]$Finish
)

$ErrorActionPreference = "Stop"

function Finish-Junction {
    param([string]$Src, [string]$Dst)
    if (-not (Test-Path $Dst)) { throw "Destination missing: $Dst" }
    if ((Get-Item $Src -ErrorAction SilentlyContinue).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        Write-Host "Junction already exists: $Src"
        return
    }
    if (Test-Path $Src) {
        $bak = "${Src}_bak_$(Get-Date -Format 'yyyyMMddHHmmss')"
        Write-Host "==> Move $Src -> $bak"
        try {
            Rename-Item -LiteralPath $Src -NewName (Split-Path $bak -Leaf) -ErrorAction Stop
            Remove-Item -LiteralPath $bak -Recurse -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Host "WARN: cannot remove D copy (folder in use). Use E:\ path in Cursor."
            Write-Host $_.Exception.Message
            return
        }
    }
    cmd /c mklink /J "$Src" "$Dst" | Out-Null
    Write-Host "Junction: $Src -> $Dst"
}

if (-not $Finish) {
    if (-not (Test-Path $Source)) {
        if (Test-Path $Dest) { Write-Host "Project already at $Dest"; exit 0 }
        throw "Source not found: $Source"
    }
    $srcNorm = (Resolve-Path $Source).Path.TrimEnd('\')
    $dstNorm = $Dest.TrimEnd('\')
    if ($srcNorm -eq $dstNorm) { Write-Host "Already at $Dest"; exit 0 }

    Write-Host "==> Copy $Source -> $Dest"
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    robocopy $Source $Dest /E /XD ".git" "__pycache__" "node_modules" ".venv" /XF "*.pyc" /NFL /NDL /NJH /NJS /nc /ns /np
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed: exit $LASTEXITCODE" }

    $dupGo = Join-Path $Dest "tools\go"
    if (Test-Path $dupGo) {
        Write-Host "==> Remove duplicate tools\go"
        Remove-Item -Recurse -Force $dupGo
    }

    [Environment]::SetEnvironmentVariable("PIGEON_PROJECT_ROOT", $Dest, "User")
    [Environment]::SetEnvironmentVariable("PIGEON_ROOT", $Dest, "User")

    $finishScript = Join-Path $Dest "scripts\migrate_project_to_e.ps1"
    Write-Host "==> Finish junction (new process)"
    Start-Process powershell -Wait -ArgumentList @(
        "-ExecutionPolicy", "Bypass", "-File", $finishScript,
        "-Dest", $Dest, "-Source", $Source, "-Finish"
    )

    Write-Host "Done. Primary path: $Dest"
    exit 0
}

Finish-Junction -Src $Source -Dst $Dest
Write-Host "Finish complete."
