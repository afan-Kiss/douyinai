# Baseline Snapshot

- **Created**: 2026-07-05
- **Purpose**: 纯协议实验，与现有系统完全隔离

## 未改动的现有项目

| 路径 | 说明 |
|------|------|
| `D:\douyin-customer-assistant` | CDP 客服助手（8799）— **未修改** |
| `D:\douyin-customer-assistant\doudian-cdp` | CDP 抓包/发送 — **未修改** |
| `D:\douyin-ai-rag` | 本地 RAG — **未修改** |
| `E:\douyin-ai-data` | 数据目录 — **未修改** |

## 本仓库来源

- 解析器自 `doudian-cdp/src/monitor/` 与 `src/sender/ws_frame_builder.py` **复制**并改 import，不反向依赖原项目。
- 参考抓包自 `doudian-cdp/captures/raw/` 复制 6 条样本到 `captures/reference/`。

## 新抓包放这里

`D:\douyin-pigeon-protocol\captures\live\`
