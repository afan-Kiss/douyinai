# pigeon-feige — Go 桌面壳 + 纯协议 worker

## 构建

```powershell
cd D:\douyin-pigeon-protocol
powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1
```

或手动：

```powershell
cd desktop\pigeon-feige
go build -o ..\..\dist\pigeon-feige.exe .
```

## 架构

| 组件 | 说明 |
|------|------|
| `main.go` | WebView2 壳，默认启动 **Go HTTP API** (:8765) |
| `internal/api` | REST 路由，与 `desktop/ui` 前端对齐 |
| `internal/protocol` | Go 原生：session.json、CSRF HEAD |
| `internal/bridge` | JSON RPC → `python run.py go-bridge` |
| `go_bridge.py` | Python 纯协议：a_bogus、xundan、WS、prepare-pure |

环境变量：

- `PIGEON_PYTHON_API=1` — 退回旧模式（Python `serve-api` 常驻）
- `PIGEON_PURE_ONLY=1` — bridge worker 严格纯协议
- `PIGEON_DEV=1` — 打印 Python stderr

## Bridge 调试

```powershell
echo '{"action":"ping"}' | python run.py go-bridge
echo '{"action":"prepare_pure","params":{}}' | python run.py go-bridge
echo '{"action":"conv_list","params":{"size":20}}' | python run.py go-bridge
```
