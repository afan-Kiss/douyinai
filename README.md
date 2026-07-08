# douyin-pigeon-protocol

抖店飞鸽 **纯协议** 实验项目，与 `douyin-customer-assistant` / `doudian-cdp` **完全独立**。

## 目标

| 能力 | 模块 | 状态 |
|------|------|------|
| 消息监听 | `ws_client.py` | 框架就绪，需有效 WS 会话 |
| 上下文获取 | `context.py` + HTTP | 框架就绪，需 Cookie + sign |
| 发送消息 | `send.py` + WS 模板 | 可离线 build，live 待验证 |
| 当前会话订单 | `order.py` | 框架就绪，需 Cookie + sign |

默认 **dry-run**（不发真实网络请求）。加 `--live` 才走 live。

## 快速开始

```powershell
cd D:\douyin-pigeon-protocol
pip install -r requirements.txt

# 查看抓包索引
python run.py index-captures

# 从抓包提取 session 模板
python run.py extract-session

# 离线解析一条 WS 入站
python run.py replay --file captures\reference\20260701_131719_415414_ws_frame_received.json

# 离线构造发送帧（不发出）
python run.py build-send --text "亲，在的，您看下证书哦"

# live 测试（需先补全 session/session.json 的 cookies）
python run.py orders --user-id AQBdQnt0Q7AWzqKFOxlUrI3wHF9PP0l8Wt61aPf6eWv91AWCzTzkx3_xWfHw2T1AWGOqMl6F6KKmD1Yi95Uz8qLI --live
python run.py listen --timeout 60 --live
python run.py send --text "测试" --live
```

## 目录

```
douyin-pigeon-protocol/
├── captures/
│   ├── reference/   # 从 doudian-cdp 复制的样本
│   └── live/        # 你新抓的包放这里
├── session/
│   └── session.json # extract-session 或手动填写
├── src/pigeon_protocol/
│   ├── http_client.py
│   ├── ws_client.py
│   ├── context.py
│   ├── order.py
│   ├── send.py
│   └── parsers/     # 从 doudian-cdp 复制的解析器
├── CAPTURE_GUIDE.md # 抓包清单（给你用）
└── BASELINE.md      # 与旧项目隔离说明
```

## 与旧系统关系

- **不修改** `D:\douyin-customer-assistant`
- 旧系统继续用 Chrome CDP；本项目尝试去掉 Chrome
- 验证通过后再考虑合并

详见 [CAPTURE_GUIDE.md](./CAPTURE_GUIDE.md)
