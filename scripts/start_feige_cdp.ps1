# Launch Feige Chrome with CDP (reuse douyin-customer-assistant profile)
$ErrorActionPreference = "Continue"
$chrome = "C:\Users\1\AppData\Local\Google\Chrome\Application\chrome.exe"
$profile = "D:\douyin-customer-assistant\data\chrome-profile"
$cdpPort = if ($env:CDP_PORT) { $env:CDP_PORT } else { 9222 }
$feige = "https://im.jinritemai.com/pc_seller_v2/main"

function Test-Cdp {
  try {
    (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$cdpPort/json/version").StatusCode | Out-Null
    return $true
  } catch { return $false }
}

if (-not (Test-Path $chrome)) { Write-Error "Chrome not found: $chrome"; exit 1 }
if (-not (Test-Cdp)) {
  Write-Host "[cdp] starting Chrome port $cdpPort profile $profile"
  Start-Process -FilePath $chrome -ArgumentList @(
    "--remote-debugging-port=$cdpPort",
    "--user-data-dir=$profile",
    "--no-first-run",
    "--no-default-browser-check",
    $feige
  )
  for ($i = 0; $i -lt 15; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Cdp) { Write-Host "[cdp] ready"; exit 0 }
  }
  Write-Error "[cdp] timeout waiting for port $cdpPort"
  exit 1
}
Write-Host "[cdp] already running on $cdpPort"
exit 0
