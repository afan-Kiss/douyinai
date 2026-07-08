# 抓包指南 — 纯协议项目所需样本

请把新抓包 JSON 放到：

```
D:\douyin-pigeon-protocol\captures\live\
```

文件名格式与 doudian-cdp 一致即可（`YYYYMMDD_HHMMSS_xxx.json`）。

---

## 你需要提供的 6 类抓包（按优先级）

### P0 — 没有就无法 live

#### 1. 完整 Cookie（最重要）

**方式 A（推荐）**：Chrome 登录飞鸽后，F12 → Application → Cookies → `https://im.jinritemai.com`

导出或截图这些 key（名称可能略有不同）：

- `sessionid` / `sessionid_ss`
- `sid_guard`
- `uid_tt` / `uid_tt_ss`
- `ttwid`
- 任何 `pigeon` / `doudian` / `fxg` 相关 cookie

**方式 B**：抓一条带 `Cookie:` 请求头的 HTTP 抓包（见下方 P1）

把 Cookie 粘贴到 `session/session.json` 的 `cookies` 字段，或发我一份 **脱敏后** 的结构（值可打码，保留 key 名）。

---

#### 2. WS 连接建立（ws_created）

打开飞鸽、刷新页面时产生。

需要文件类型：`ws_created.json`

必须包含完整 URL，例如：

```
wss://ws.fxg.jinritemai.com/ws/v2?token=...&device_id=...&access_key=...&pigeon_sign=...
```

**操作**：登录飞鸽 → 刷新一次 → 从 doudian-cdp 的 `captures/raw/` 复制最新 `ws_created` 到 `captures/live/`

---

#### 3. 出站发送文本（ws_frame_sent）

**操作**：

1. 打开一个买家会话
2. **手动发送一条纯文字**（例如：`协议抓包测试123`）
3. 复制对应的 `ws_frame_sent.json`

这是 `send.py` 构造发送帧的模板，**必需**。

---

### P1 — 监听与上下文

#### 4. 入站买家消息（ws_frame_received）

**操作**：

1. 让买家发一条文字（或用小号发）
2. 复制 `ws_frame_received.json`（payload 要完整）

用于验证 `listen` / 解析器。

---

#### 5. 历史消息 HTTP（get_history_msg_sub）

**操作**：

1. 点开某个会话，触发加载聊天记录
2. 在抓包中找 URL 含 `get_history_msg_sub` 的 POST
3. 复制 `http_body.json` 或 `http_response.json`

需要包含：

- 完整 URL（含 `msToken` / `a_bogus` / `verifyFp`）
- `post_data` JSON body
- `response_body`（含 `msg_body_list` 最佳）

---

#### 6. 订单查询 HTTP（order/query）

**操作**：

1. 点开 **有订单的买家** 会话
2. 等右侧订单面板加载
3. 复制 URL 含 `backstage/cmpoent/order/query` 的 POST

需要包含：

- 完整 URL + query 参数
- `post_data` 里的 `security_user_id`
- `response_body`（成功或空数组都要）

---

### P2 — 增强（可选但很有用）

| # | 场景 | URL 关键词 | 用途 |
|---|------|-----------|------|
| 7 | 会话列表 | `fuzzySearchConversation` | 多会话轮询 |
| 8 | 用户卡片 | `get_user_card` | 买家昵称 |
| 9 | 商品咨询 | `get_consulting_products` | 商品上下文 |
| 10 | frontier WS | `frontier.snssdk.com` ws_created | 备用长连接 |

---

## 推荐抓包方式

### 方式 1：继续用现有 doudian-cdp（最简单）

1. 运行 `D:\douyin-customer-assistant\start-doudian-cdp.cmd`
2. 登录飞鸽，完成上述操作
3. 从 `D:\douyin-customer-assistant\doudian-cdp\captures\raw\` 复制文件到  
   `D:\douyin-pigeon-protocol\captures\live\`

### 方式 2：Chrome DevTools 手动导出

1. F12 → Network → 勾选 Preserve log
2. Filter: `WS` 或 `pigeon`
3. 右键请求 → Copy → Copy as cURL
4. 把 cURL 发我，或自己转成 JSON 放到 `captures/live/`

### 方式 3：EditThisCookie / Cookie-Editor 插件

导出 `im.jinritemai.com` + `pigeon.jinritemai.com` 的 Cookie JSON。

---

## 一次完整抓包流程（5 分钟）

```
1. 启动 doudian-cdp，登录飞鸽
2. 刷新页面                    → 拿 ws_created
3. 打开买家 A，看历史消息       → 拿 get_history_msg_sub
4. 看订单面板                  → 拿 order/query
5. 手动发送「测试消息」         → 拿 ws_frame_sent
6. 让买家回一条                → 拿 ws_frame_received
7. 复制 5~6 个 json 到 captures/live/
8. 在本项目运行:
   python run.py extract-session
   python run.py status
```

---

## 发给我时的格式

可以直接发：

1. `captures/live/` 里 5~10 个 json 文件
2. 或 Cookie JSON（**不要发到公开渠道**）
3. 说明：哪个买家有订单、哪条是你发的测试消息

**安全提醒**：

- Cookie / token 等同于登录凭证，勿公开
- 发前可只保留 key 名，值打码，我帮你看结构

---

## 本项目收到抓包后会做什么

```powershell
python run.py extract-session    # 自动提取 token/ws_url/headers
python run.py replay --file ...  # 验证解析
python run.py build-send --text "测试"
python run.py orders --user-id AQ... --live
python run.py listen --live
python run.py send --text "测试" --live
```

---

## 当前 reference 样本说明

`captures/reference/` 已有 6 条 2026-07-01 样本：

- WS 连接 / 入站 ACK / 出站帧
- 订单 query（该买家无订单，total=0）
- 普通 HTTP

**缺少完整 Cookie**，所以 reference 只能离线解析，不能 live 调用。
