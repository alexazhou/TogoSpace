# Multi-Agent 聊天系统 - 实施路线图

## 项目概述

构建一个支持多个 AI Agent 互相聊天的演示系统，每个 Agent 通过配置文件定义其角色和性格，支持在多个聊天室中并发通信。

---

## 版本概览

### V1: 双 Agent 单房间

实现最简单的多 Agent 聊天场景，验证核心概念：

- 固定两个 Agent（Alice 和 Bob），每个通过配置文件定义
- 在单个聊天室中轮流对话
- 使用 asyncio 实现基础调度机制
- 固定轮次后自动结束对话
- 验证 Agent 能表现出配置的性格特征

### V2: 多 Agent 单房间

扩展 V1，验证架构的可扩展性：

- 支持从配置文件动态加载任意数量的 Agent（3+ 个）
- 实现基于 asyncio 的消息队列机制
- 在单个聊天室内，多个 Agent 轮流对话
- 验证调度器能正确处理多个 Agent 的并发请求

### V3: 多 Agent 多房间

扩展 V2，支持更复杂的场景：

- 支持创建多个独立的聊天室
- Agent 可以选择加入不同的聊天室
- Agent 可以同时在多个聊天室中活跃
- 消息正确路由到对应的聊天室
- Agent 能感知聊天室内的其他参与者

### V4: 状态持久化与聊天恢复

扩展 V3，增加数据持久化能力：

- 所有消息持久化到存储（JSON/SQLite）
- 支持保存完整的聊天状态
- 系统重启后能恢复聊天状态
- 支持查询历史消息和聊天回溯
- 支持导出聊天记录

---

## 技术栈

| 类别 | 技术 | 版本 |
|------|------|------|
| 编程语言 | Python | 3.11+ |
| HTTP 客户端 | aiohttp | 3.13+ |
| 异步框架 | asyncio | 内置 |
| 配置管理 | JSON | - |
| 持久化 | JSON/SQLite | - |

---

## 相关文档

### V1
- [产品文档](./versions/v1/v1_step1_product.md)
- [技术文档](./versions/v1/v1_step2_technical.md)
- [开发任务表](./versions/v1/v1_step3_tasks.md)

### V2
- [产品文档](./versions/v2/v2_step1_product.md)
- [技术文档](./versions/v2/v2_step2_technical.md)
- [开发任务表](./versions/v2/v2_step3_tasks.md)

### V3
- [产品文档](./versions/v3/v3_step1_product.md)
- [技术文档](./versions/v3/v3_step2_technical.md)
- [开发任务表](./versions/v3/v3_step3_tasks.md)

### 其他版本
- [V4: 状态持久化与聊天恢复](./versions/v4_persistence.md)
