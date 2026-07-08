# Build portable pigeon-feige distribution layout
# Usage: .\scripts\build_portable.ps1 [-SkipDesktop] [-SkipPython]
param(
    [switch]$SkipDesktop,
    [switch]$SkipPython
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Dist = Join-Path $Root "dist\pigeon-feige"
$Runtime = Join-Path $Dist "runtime"

function Ensure-Dir($p) { New-Item -ItemType Directory -Force -Path $p | Out-Null }

Write-Host "==> Portable layout: $Dist"
Ensure-Dir $Dist
Ensure-Dir $Runtime
Ensure-Dir (Join-Path $Dist "accounts")
Ensure-Dir (Join-Path $Dist "logs")
Ensure-Dir (Join-Path $Dist "analysis")
Ensure-Dir (Join-Path $Dist "captures")

# Core project files (protocol worker)
$CopyItems = @(
    "run.py",
    "requirements.txt",
    "src",
    "scripts",
    "desktop\ui"
)
foreach ($item in $CopyItems) {
    $src = Join-Path $Root $item
    $dst = Join-Path $Dist $item
    if (-not (Test-Path $src)) { continue }
    if (Test-Path $src -PathType Container) {
        if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
        Copy-Item -Recurse -Force $src $dst
    } else {
        Copy-Item -Force $src $dst
    }
}

# Optional: seed accounts from dev machine (if exists)
$DevAccounts = Join-Path $Root "accounts"
if (Test-Path $DevAccounts) {
    Write-Host "==> Copy accounts registry (no shop data unless present)"
    $DstAccounts = Join-Path $Dist "accounts"
    Copy-Item -Recurse -Force $DevAccounts $DstAccounts
}

# Desktop exe
if (-not $SkipDesktop) {
    $DesktopScript = Join-Path $Root "scripts\build_desktop.ps1"
    if (Test-Path $DesktopScript) {
        & $DesktopScript
        $Exe = Join-Path $Root "dist\pigeon-feige.exe"
        if (Test-Path $Exe) {
            Copy-Item -Force $Exe (Join-Path $Dist "pigeon-feige.exe")
        }
    }
}

# Embedded Python (optional)
$PyEmbed = Join-Path $Root "runtime\python"
if (-not $SkipPython -and (Test-Path $PyEmbed)) {
    Write-Host "==> Copy embedded Python"
    $PyDst = Join-Path $Runtime "python"
    if (Test-Path $PyDst) { Remove-Item -Recurse -Force $PyDst }
    Copy-Item -Recurse -Force $PyEmbed $PyDst
}

$NodeEmbed = Join-Path $Root "runtime\node"
if (Test-Path $NodeEmbed) {
    Write-Host "==> Copy embedded Node"
    $NodeDst = Join-Path $Runtime "node"
    if (Test-Path $NodeDst) { Remove-Item -Recurse -Force $NodeDst }
    Copy-Item -Recurse -Force $NodeEmbed $NodeDst
}

# Launcher
@'
@echo off
setlocal
cd /d "%~dp0"
set PIGEON_PROJECT_ROOT=%~dp0
set PIGEON_ROOT=%~dp0
set PIGEON_STANDALONE=1
set PIGEON_NO_CDP=1
set PIGEON_WS_HOST=jinritemai
if exist "%~dp0runtime\python\python.exe" set PIGEON_PYTHON=%~dp0runtime\python\python.exe
if exist "%~dp0runtime\node\node.exe" set PIGEON_NODE=%~dp0runtime\node\node.exe
start "" "%~dp0pigeon-feige.exe"
'@ | Set-Content -Encoding ASCII (Join-Path $Dist "启动飞鸽.bat")

@'
# 抖店飞鸽 AI 客服 — 便携版

## 首次使用
1. 双击 `启动飞鸽.bat` 或 `pigeon-feige.exe`
2. 点击 **扫码登录** → 抖音/抖店 App 扫码
3. 自动预热协议，即可收消息、回复

## 多账号
- 顶栏 **+ 账号** → 扫码登录第二个店
- 下拉切换店铺（自动切换会话与监听）

## 换机（推荐）
**方式 A — 会话包**
1. 旧电脑：顶栏 **导出会话** → 得到 `accounts/shop_xxx/pigeon_session_pack.zip`
2. 新电脑：把整个 `pigeon-feige` 文件夹复制过去，或仅导入 zip（顶栏 **导入会话**）
3. 启动即用，无需浏览器

**方式 B — 只扫码**
- 新电脑安装后直接扫码登录（无需导包）

## 健康检查
```
runtime\python\python.exe scripts\health_check.py
```

## 目录
- `pigeon-feige.exe` — 桌面壳 + 本地 API
- `accounts/` — 多账号数据（每店独立 session + bundle）
- `runtime/python` — 内嵌 Python（需 embed 或手动放置）
- `runtime/node` — bdms 签名
- `logs/` — 二维码等日志

## 会话过期
- 点击 **扫码登录** 重新登录，或顶栏 **导入会话** 恢复备份包
'@ | Set-Content -Encoding UTF8 (Join-Path $Dist "README.txt")

Write-Host "Done: $Dist"
Write-Host "Next: place runtime\python + runtime\node, then run 启动飞鸽.bat"
