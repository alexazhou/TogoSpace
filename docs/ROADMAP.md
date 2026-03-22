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
- 各聊天室之间数据完全隔离：同名 Agent 在不同房间是独立实例，各自只感知本房间的消息历史

### V3.1: 跨房间上下文感知

扩展 V3，打破房间数据隔离：

- 同时参与多个房间的 Agent 能感知其他房间的消息历史
- Agent 可在当前房间发言时引用其他房间的话题内容
- 支持通过配置开关控制是否启用跨房间感知
- 各房间的发言规则和并发调度机制保持不变

### V3.2: 以 Agent 为中心的事件驱动调度

重构调度模型，从房间视角改为 Agent 视角：

- 每个 Agent 持有独立的事件队列，被动接收来自各房间的新消息事件
- 调度器并发启动所有 Agent 的事件循环，不再控制发言顺序
- Agent 主动消费事件队列，自行决定何时在哪个房间发言
- 跨房间上下文感知能力保留（同 V3.1）
- 发言顺序由事件到达顺序自然决定，更贴近真实的异步通信模型

### V3.3: 自动化测试 (已完成)

引入单元测试与集成测试，为后续功能开发提供质量保障：

- 为核心模块（`agentService`、`roomService`、`schedulerService`、`funcToolService`）编写单元测试
- 使用 mock 隔离外部依赖（LLM API、消息总线），确保测试稳定可重复
- 编写集成测试，验证多 Agent 完整对话流程（消息路由、轮次调度、tool call 执行）
- 配置 CI 自动运行测试，覆盖率达到基准线
- 建立测试目录结构与命名规范，为后续版本测试提供模板

### V4: Web API 与实时可视化接入 (已完成)

在现有 service 层之上暴露 HTTP + WebSocket 接口，供可视化程序和外部系统实时接入：

- 提供 REST 接口，查询 Agent 列表、Room 列表及消息历史
- 提供 WebSocket 接口，订阅实时消息事件（Agent 发言、状态变更）
- 客户端无需轮询，服务端主动推送新消息
- 支持多客户端同时订阅，互不干扰
- 接口设计与 service 层解耦，不侵入核心调度逻辑

### V5: 终端可视化前端 (已完成)

基于 V4 的 HTTP/WebSocket 接口，开发终端 TUI 前端：

- 用 Textual 实现终端聊天室观察界面，风格类似桌面聊天软件
- 左侧房间列表 + 成员展示，键盘或鼠标切换房间
- 右侧实时滚动的对话气泡，不同 Agent 左右分列
- 通过 WebSocket 实时接收新消息，无需轮询
- 非当前房间有新消息时显示未读角标
- 纯观察模式（只读），与后端进程完全解耦

### V6: 交互式协作 (交互简化版)

引入人类“操作者”角色，并对房间进行类型化隔离以简化交互逻辑：

- **房间分类**：
    - **单聊 (Private)**：1 人类 + 1 Agent，采用严格的回合制交互，人类不发言 Agent 不动。
    - **群聊 (Group)**：多 Agent 自治模拟，人类仅作为观察者（同 V5）。
- **交互 TUI**：在单聊房间界面激活输入框，支持人类发送消息。
- **混合通信**：采用 HTTP POST 发送人类消息，WebSocket 实时订阅并展示回复。
- **逻辑简化**：避开多 Agent 并发抢答，优先打通“人类输入 -> Agent 定向回复”的核心链路。

### V7: 团队化组织与多租户隔离 (已完成)

引入 "Team" 概念作为顶层容器，支持更复杂的并发模拟场景：

- **顶层容器**：引入 Team 概念，将相关的 Agent 和 Room 进行逻辑分组管理。
- **资源标识符**：统一采用命名空间格式标识 Agent 和 Room，确保在多团队并发时的全局唯一性。
- **配置隔离**：不同 Team 拥有独立的成员名单、房间配置和运行参数，实现多租户级别的逻辑隔离。
- **多团队并发**：支持同时启动多个独立的团队模拟任务，各团队调度逻辑互不干扰。
- **扩展上下文模型**：同团队内的 Agent 跨房间共享对话感知，为复杂的跨场景协作奠定基础。

### V8: Agent 执行能力

集成 Claude Code Agent SDK，赋予 Agent 真实的执行能力，使其从"只会说话"升级为"能动手做事"：

- **执行能力**：Agent 可按配置读写文件、运行代码、搜索网络，并将执行结果以自然语言反馈到聊天室
- **混合协作**：普通对话 Agent 与具备执行能力的 Agent 可在同一聊天室共存协作

### V9: 数据持久化与重启恢复

在现有运行时能力之上补齐状态落盘与恢复机制，使服务在重启后可以恢复之前的状态，为后续手动继续运行提供基础：

- **状态持久化**：将 Team、Agent、Room、消息历史、事件队列和关键运行状态持久化到本地存储
- **重启恢复**：服务启动时自动加载最近一次快照或日志，恢复房间上下文、Agent 视图与调度状态
- **手动续跑**：恢复完成后默认不自动恢复聊天室调度，需由用户显式触发继续运行
- **消息连续性**：避免服务重启后丢失历史消息、未处理事件或房间成员关系，确保对话可连续进行
- **一致性保障**：为消息写入、事件消费和状态更新设计明确的落盘时机，降低异常退出导致的数据不一致风险
- **可观测与运维**：提供持久化开关、存储目录配置、状态检查与恢复日志，便于排查恢复过程中的问题

---

## 技术栈

| 类别 | 技术 | 版本 |
|------|------|------|
| 编程语言 | Python / Go | 3.11+ / 1.20+ |
| Web 框架 | Tornado | 6.3+ |
| HTTP 客户端 | aiohttp | 3.13+ |
| 异步框架 | asyncio | 内置 |
| 配置管理 | JSON | - |
| 持久化 | JSON/SQLite | - |
| TUI 框架 | Textual | 0.80+ |

## 辅助组件

### Go Simu Terminal
一个轻量级的终端模拟器，用于为 TUI 提供底层渲染和输入模拟能力，支持 CJK 宽字符渲染并能输出 SVG 快照。详见 [Go Simu Terminal 文档](./go_simu_terminal.md)。

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

### V3.1
- [产品文档](./versions/v3.1/v3.1_step1_product.md)
- [技术文档](./versions/v3.1/v3.1_step2_technical.md)

### V3.2
- [产品文档](./versions/v3.2/v3.2_step1_product.md)
- [技术文档](./versions/v3.2/v3.2_step2_technical.md)

### V3.3
- 待规划

### V4
- [产品文档](./versions/v4/v4_step1_product.md)
- [技术文档](./versions/v4/v4_step2_technical.md)

### V5
- [产品文档](./versions/v5/v5_step1_product.md)

### V6
- [产品文档](./versions/v6/v6_step1_product.md)
- [技术文档](./versions/v6/v6_step2_technical.md)

### V7
- [产品文档](./versions/v7/v7_step1_product.md)
- [技术文档](./versions/v7/v7_step2_technical.md)

### V8
- [产品文档](./versions/v8/v8_step1_product.md)
- [技术文档](./versions/v8/v8_step2_technical.md)

### V9
- [产品文档](./versions/v9/v9_step1_product.md)
- [技术文档](./versions/v9/v9_step2_technical.md)
- [开发任务表](./versions/v9/v9_step3_tasks.md)
