# 抓包清单 — 补齐纯协议缺口

当前算法审计 **6/6**；剩余缺口需要你在 **已登录飞鸽 Chrome** 里抓样本。

---

## 一、订单实时 API（修 `10001010A`）

**你要做：**

1. Chrome 打开 https://im.jinritemai.com ，扫码登录
2. F12 → Network → 勾选 **Preserve log**
3. **右键 Network 面板 → 勾选 Include cookies**（必须）
4. 点开任意买家会话，等订单区域加载
5. 右键 → **Save all as HAR with content**
6. 保存为 `登录_新.har`

**导入：**

```powershell
cd D:\douyin-pigeon-protocol
python run.py import-har --file C:\Users\1\Desktop\登录_新.har
python run.py session-doctor --fix
python scripts/test_node_order_relay.py
```

**成功标志：** `code: 0`（不是 `10001010A`）

---

## 二、WS 发送缺口（145 个 textB 长度）

当前纯协议只覆盖 **55/200** 长度。要补模板，需在飞鸽里 **真实发送** 带签名的文本帧，并导出 HAR。

### 方式 A：HAR 抓 WS（推荐，你说可以抓包）

1. 同上登录飞鸽，打开一个买家聊天
2. Network → 筛选 **WS**，选中 `wss://ws.fxg.jinritemai.com/...`
3. 在聊天框按下面表格 **逐条发送**（复制粘贴即可）
4. 每发 3–5 条可导一次 HAR，或全部发完再导
5. 保存为 `ws_send.har`

**优先发送这些长度（复制到飞鸽输入框）：**

| textB | 发送内容（UTF-8 精确字节） |
|-------|---------------------------|
| 7 | `好好1` |
| 8 | `好好12` |
| 61 | `好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好好1` |
| 62 | 同上 + `2` |
| 75 | 25 个 `好` |
| 76 | 25 个 `好` + `1` |
| 79 | 26 个 `好` + `1` |
| 80 | 26 个 `好` + `12` |
| 1 | `1` |
| 2 | `12` |
| 3 | `好` |
| 4 | `好1` |
| 5 | `好12` |

查看完整缺口列表：

```powershell
python run.py ws-gap-plan
```

**导入 WS 模板：**

```powershell
python run.py import-har-ws --file C:\Users\1\Desktop\ws_send.har
python scripts/test_ws_bucket_coverage.py
python run.py foundation-status
```

**成功标志：** `supported_count_1_200` 上升；`ws-gap-plan` 里对应长度显示 `has_template: true`

### 方式 B：Chrome 调试端口自动采集（可选）

```powershell
# 关闭所有 Chrome 后，用调试端口启动：
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
# 登录飞鸽并打开买家会话，然后：
cd D:\douyin-pigeon-protocol
python run.py bootstrap --full
```

---

## 三、226B 动态重签（最难，需多样本）

要逆向 IM SDK inner 签名，需要 **同一买家、不同文本、不同 client_message_id** 的多条发送帧。

**你要做：**

1. 用方式 A 或 B 采集 **至少 4 个桶** 各 2 条：textB = 6, 9, 77, 78（已有可跳过）
2. 额外采集 textB = 7, 61, 79 各 1 条（新桶边界）
3. 把 `ws_send.har` 或 `captures/live/ws_sign/` 发给我 / 再跑 `import-har-ws`

---

## 四、你不需要做的

| 项 | 状态 |
|----|------|
| a_bogus 算法 | ✅ Python = Node payload |
| WS 收/上下文/列表 | ✅ 纯协议可用 |
| textB 6 / 9–60 / 77 / 78 | ✅ 已有模板 |

---

## 五、导入后自检

```powershell
python run.py audit-foundation      # 目标 6/6
python run.py ws-gap-plan           # 看还剩哪些长度
python run.py foundation-status     # ok: true
```

把 HAR 放到桌面后告诉我文件名，我可以继续跑导入和 RE。
