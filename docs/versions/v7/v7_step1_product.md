# V7: 团队化组织与多租户隔离 - 产品文档

## 目标

在 V6 交互式协作的基础上，引入 **Team (团队)** 概念作为系统的顶层组织容器。通过团队化重构，支持在同一套服务架构下并发运行多个互不干扰的 Agent 协作任务，实现多租户级别的逻辑隔离和资源管理。

---

## 功能特性

- **顶层团队容器**：引入 Team 概念，将相关的 Agent、Room 和配置进行逻辑分组。
- **命名空间隔离**：所有资源（Agent, Room）采用 `name@team` 的全局唯一标识符，确保跨团队操作的明确性。
- **多租户并发模拟**：支持从多个配置文件加载不同的 Team，各 Team 拥有独立的调度逻辑和生命周期，互不干扰。
- **灵活的资源配置**：不同 Team 可配置不同的全局参数（如最大工具调用次数、重试策略等）。
- **扩展的上下文模型**：同 Team 内的 Agent 实例在不同房间中保持一致性，支持跨房间的对话历史感知。
- **系统初始化重构**：支持按 Team 维度进行资源加载和启动，提升系统的模块化程度。

---

## 资源标识与隔离

| 维度 | 隔离机制 | 说明 |
|------|---------|------|
| **Agent** | `agent@team` | 同名 Agent 在不同 Team 中是完全独立的实例，拥有各自的私有历史。 |
| **Room** | `room@team` | 房间名称在 Team 内唯一，跨 Team 可重名且数据完全隔离。 |
| **调度** | 独立事件循环 | 每个 Team 的消息流转和任务执行在逻辑上相互独立。 |
| **配置** | 独立 JSON 文件 | 每个 Team 的成员、房间和参数由独立的配置文件定义。 |

---

## 效果演示

### 配置示例

同时启动两个 Team：
1. **Team: CustomerService** (客服团队)
   - 成员: Alice (客服), Bob (技术支持)
   - 房间: `Support@CustomerService`
2. **Team: CreativeWriting** (创作团队)
   - 成员: Alice (诗人), Charlie (评论家)
   - 房间: `PoetryRoom@CreativeWriting`

### 对话示例

**[Team: CustomerService]**
```
--- Support@CustomerService ---
Alice@CustomerService: 您好！有什么可以帮您的？
Bob@CustomerService: 我正在查看后台日志，请稍等。
```

**[Team: CreativeWriting]**
```
--- PoetryRoom@CreativeWriting ---
Alice@CreativeWriting: 寂静的夜，思绪如潮水般涌动。
Charlie@CreativeWriting: 这一句的意境非常深远，特别是“涌动”二字。
```

虽然两个团队都有叫 Alice 的 Agent，但她们的角色（客服 vs 诗人）和所处上下文完全不同，互不干扰。

---

## 验收标准

- [ ] 支持从配置目录动态加载多个 Team 配置文件。
- [ ] Agent 和 Room 在内部管理中均带有所属 Team 的标识（命名空间）。
- [ ] 不同 Team 的对话任务能够并发运行，且消息路由正确（不串号）。
- [ ] 某一 Team 的异常（如 API 调用失败）不影响其他 Team 的正常运行。
- [ ] Web API 和 TUI 能够正确展示所属 Team 的层级关系。
- [ ] 支持在配置文件中为不同 Team 设置差异化的运行参数。

---

## 使用说明

### 配置文件结构

每个 Team 对应一个独立配置文件：
- 定义团队名称。
- 定义该团队内的所有房间配置。
- 定义该团队引用的 Agent 成员名单。
- 定义团队特定的运行参数（如最大轮次、工具调用限制等）。

### 运行

系统启动时会自动扫描配置目录并加载所有定义的 Team：
```bash
python src/main.py
```

### 观察

在日志或 Web UI 中，资源将以 `name@team` 形式展示。
