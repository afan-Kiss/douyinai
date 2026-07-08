# Install Go for pigeon-feige build — 默认装到 E:\devtools\go（不占用 C 盘）
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Tools = Join-Path $Root "tools"
$DevRoot = if ($env:PIGEON_DEV_ROOT) { $env:PIGEON_DEV_ROOT } else { "E:\devtools" }
$PreferredGo = Join-Path $DevRoot "go"
$PortableGoDir = Join-Path $Tools "go"
$Version = "1.24.4"
$ZipName = "go$Version.windows-amd64.zip"
$Url = "https://go.dev/dl/$ZipName"
$ZipPath = Join-Path $Tools $ZipName
$GoPathDir = Join-Path $DevRoot "gopath"

function Test-GoExe($path) {
    return (Test-Path $path) -and (& $path version 2>$null)
}

function Ensure-GoPath {
    if (-not (Test-Path $GoPathDir)) {
        New-Item -ItemType Directory -Force -Path $GoPathDir | Out-Null
    }
    [Environment]::SetEnvironmentVariable("GOPATH", $GoPathDir, "User")
    [Environment]::SetEnvironmentVariable("GOROOT", $PreferredGo, "User")
}

function Add-GoToUserPath($binDir) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $clean = ($userPath -split ";" | Where-Object {
        $_ -and $_ -notlike "C:\Go\bin*" -and $_ -ne $binDir
    }) -join ";"
    if ($clean -notlike "*$binDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$binDir;$clean", "User")
        $env:Path = "$binDir;$env:Path"
        Write-Host "Added $binDir to user PATH"
    }
}

# 1) E:\devtools\go（推荐）
$PreferredBin = Join-Path $PreferredGo "bin\go.exe"
if (Test-GoExe $PreferredBin) {
    & $PreferredBin version
    Ensure-GoPath
    Add-GoToUserPath (Join-Path $PreferredGo "bin")
    Write-Host "Go ready at $PreferredGo"
    exit 0
}

# 2) 旧 C:\Go（提示迁移）
$LegacyGo = "C:\Go\bin\go.exe"
if (Test-GoExe $LegacyGo) {
    & $LegacyGo version
    Write-Host "WARNING: Go still on C:\Go — run .\scripts\migrate_dev_to_e.ps1 to move to E:\devtools\go"
    exit 0
}

# 3) 项目内便携 tools\go
$PortableBin = Join-Path $PortableGoDir "bin\go.exe"
if (Test-GoExe $PortableBin) {
    & $PortableBin version
    Write-Host "Go already installed at $PortableGoDir (project-local)"
    exit 0
}

# 4) 下载到 E:\devtools\go
New-Item -ItemType Directory -Force -Path $DevRoot | Out-Null
New-Item -ItemType Directory -Force -Path $Tools | Out-Null
Write-Host "Downloading $Url -> $PreferredGo ..."
if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
    curl.exe -L -o $ZipPath $Url
} else {
    Invoke-WebRequest -Uri $Url -OutFile $ZipPath -UseBasicParsing
}
if (Test-Path $PreferredGo) { Remove-Item -Recurse -Force $PreferredGo }
Expand-Archive -Path $ZipPath -DestinationPath $DevRoot -Force
$extracted = Join-Path $DevRoot "go"
if (-not (Test-Path $PreferredBin)) {
    throw "Install failed: $PreferredBin not found after extract"
}
Ensure-GoPath
Add-GoToUserPath (Join-Path $PreferredGo "bin")
& $PreferredBin version
Write-Host "Installed Go to $PreferredGo (GOPATH=$GoPathDir)"
