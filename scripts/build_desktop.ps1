# Build pigeon-feige.exe (requires Go + Python runtime on target machine)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DevRoot = if ($env:PIGEON_DEV_ROOT) { $env:PIGEON_DEV_ROOT } else { "E:\devtools" }
$GoCandidates = @(
    (Join-Path $DevRoot "go\bin\go.exe"),
    (Join-Path $Root "tools\go\bin\go.exe"),
    "C:\Go\bin\go.exe"
)
$GoBin = $null
foreach ($c in $GoCandidates) {
    if (Test-Path $c) { $GoBin = $c; break }
}
if (-not $GoBin) {
    & (Join-Path $Root "scripts\install_go.ps1")
    foreach ($c in $GoCandidates) {
        if (Test-Path $c) { $GoBin = $c; break }
    }
}
if (-not $GoBin) { throw "Go not found; run scripts\install_go.ps1" }
$AppDir = Join-Path $Root "desktop\pigeon-feige"
$OutDir = Join-Path $Root "dist"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Push-Location $AppDir
$env:GOPROXY = "https://goproxy.cn,direct"
$env:CGO_ENABLED = "0"
& $GoBin mod tidy
if ($LASTEXITCODE -ne 0) { Pop-Location; exit $LASTEXITCODE }
& $GoBin build -ldflags "-H=windowsgui" -o (Join-Path $OutDir "pigeon-feige.exe") .
Pop-Location
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Built: $(Join-Path $OutDir 'pigeon-feige.exe')"
