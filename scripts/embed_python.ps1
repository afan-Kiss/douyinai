# Embed CPython embeddable distro into runtime/python (Windows)
# Download from https://www.python.org/downloads/windows/ — "Windows embeddable package (64-bit)"
# Or set $PyZip to an existing embed zip path.
param(
    [string]$PyZip = "",
    [string]$Version = "3.12.7"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Runtime = Join-Path $Root "runtime\python"
$Site = Join-Path $Runtime "Lib\site-packages"

if (-not $PyZip) {
    $PyZip = Join-Path $Root "tools\python-embed-$Version-amd64.zip"
}
if (-not (Test-Path $PyZip)) {
    Write-Host "Place embed zip at: $PyZip"
    Write-Host "Or: .\scripts\embed_python.ps1 -PyZip C:\path\to\python-embed.zip"
    exit 1
}

if (Test-Path $Runtime) { Remove-Item -Recurse -Force $Runtime }
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null
Expand-Archive -Force -Path $PyZip -DestinationPath $Runtime

# Enable site-packages in python312._pth (adjust version in filename)
$pth = Get-ChildItem $Runtime -Filter "python*._pth" | Select-Object -First 1
if ($pth) {
    $content = Get-Content $pth.FullName
    $content = $content -replace "#import site", "import site"
    if ($content -notcontains "Lib\site-packages") {
        $content += "Lib\site-packages"
    }
    Set-Content -Path $pth.FullName -Value $content
}

New-Item -ItemType Directory -Force -Path $Site | Out-Null

# pip
$getPip = Join-Path $env:TEMP "get-pip.py"
if (-not (Test-Path $getPip)) {
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPip
}
& (Join-Path $Runtime "python.exe") $getPip --no-warn-script-location

# project deps
$req = Join-Path $Root "requirements.txt"
& (Join-Path $Runtime "python.exe") -m pip install -r $req --target $Site --no-warn-script-location
& (Join-Path $Runtime "python.exe") -m playwright install chromium 2>$null

Write-Host "Embedded Python ready: $Runtime\python.exe"
