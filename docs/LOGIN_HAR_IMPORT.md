# 用户登录后一键导入

抓 HAR 步骤（Chrome）：
1. 打开飞鸽 im.jinritemai.com，扫码登录
2. F12 → Network → 勾选 **Preserve log**
3. **右键 Network 面板 → 勾选 "Include cookies"**（否则 HAR 无 Cookie）
4. 刷新页面，随便点开一个买家会话（需有 fuzzySearchConversation / order/query 请求）
5. 右键任意请求 → Save all as HAR with content
6. 保存为 `登录.har`

> 若 HAR 无 Cookie，导入会**保留现有 session**，不会清零。

## 命令行导入

```powershell
cd D:\douyin-pigeon-protocol
python run.py import-har --file C:\Users\1\Desktop\登录.har
python run.py session-doctor --fix
python run.py standalone-status

$env:PIGEON_STANDALONE='1'
python run.py --live orders --user-id "AQC..."
```

## 桌面 EXE（推荐）

已安装 Go 1.24（便携版在 `tools\go`），构建产物：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1
# 输出: dist\pigeon-feige.exe
```

双击 `dist\pigeon-feige.exe`：
- 默认启动 **Go HTTP API**（`internal/api`）+ Python **go-bridge** 纯协议 worker
- 打开 Web 界面（WebView2）
- **登录**：扫码 / 导入 HAR
- **会话列表 / 上下文 / 订单 / WS 监听 / 发送**

退回旧 Python API：`$env:PIGEON_PYTHON_API="1"; .\dist\pigeon-feige.exe`

依赖：本机已装 Python 3 + Node（bdms 签名）+ `pip install -r requirements.txt`

也可仅启动 API：

```powershell
python run.py serve-api
# 浏览器打开 http://127.0.0.1:8765/
```

## 最近联系 HAR（会话列表）

桌面 `最近联系.har` 含 `xundan_chat_list`（GET）— 比旧的 `fuzzySearchConversation` 更准。

导入后自动写入 `analysis/bdms_browser_env.json`：
- `relayHeaders`（含 CSRF chrome hints）
- `convListTemplate`（`_v` / `queue_key` / `page_size`）

验证：

```powershell
python scripts/test_conv_list_relay.py
python run.py standalone-status   # pure_ready.conversations: true
```

自动完成：
- cookies → session.json
- ws_url 从 HAR WebSocket 条目
- order/query 请求的 relayHeaders（含 x-secsdk-csrf-token）
- HEAD 自动刷新 CSRF（curl_cffi，无 CDP）
- captures 导出到 captures/live/from_har/

会话过期时：
```powershell
python run.py session-doctor --fix   # 自动 HEAD 刷新 CSRF
python run.py import-har --file new_login.har   # 重新登录后导入
```
