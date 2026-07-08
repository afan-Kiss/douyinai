# Launch Feige Electron with CDP on port 9223 (separate from Chrome :9222)
$ErrorActionPreference = "Stop"
$exe = "E:\feige-electron\抖店工作台\1.1.7\doudian.exe"
if (-not (Test-Path $exe)) {
  $exe = (Get-ChildItem "E:\feige-electron" -Recurse -Filter "doudian.exe" | Select-Object -First 1).FullName
}
if (-not $exe) { throw "doudian.exe not found under E:\feige-electron" }
Write-Host "Launching $exe --remote-debugging-port=9223"
Start-Process -FilePath $exe -ArgumentList "--remote-debugging-port=9223" -WorkingDirectory (Split-Path $exe)
Write-Host "CDP: http://127.0.0.1:9223/json/version"
