# Agent 子类方案 vs Driver 方案

## 结论

对当前项目，更推荐 `Agent + Driver` 的组合方案，而不是为每种实现定义一个 `Agent` 子类。

简短原因：

- 当前变化轴更像“执行策略”，不是“对象身份”
- `Agent` 已经承载了较多稳定状态
- 调度、持久化、房间语义都希望统一
- 后续接入方式很可能继续增加

## 当前问题的第一性原理

先拆这个问题的本质。

系统里有两类差异：

### 一类是 Agent 身份差异

例如：

- `alice`
- `bob`
- `researcher`
- `software_engineer`

这些差异主要体现在：

- prompt
- model
- 历史
- 所属 team / room

### 另一类是 Agent 执行方式差异

例如：

- `native`
- `claude_sdk`
- `gemini_cli`

这些差异主要体现在：

- 如何组织输入
- 如何调用底层 LLM / SDK / CLI
- 如何解析输出
- 如何映射成系统认可的动作

从第一性原理看，当前真正变化更大的部分是“执行方式”，不是“Agent 身份”。

因此，更自然的建模是：

- `Agent` = 稳定实体
- `Driver` = 可替换执行策略

## 方案 A：多个 Agent 子类

示例：

```python
class Agent: ...
class NativeAgent(Agent): ...
class ClaudeSdkAgent(Agent): ...
class GeminiCliAgent(Agent): ...
```

### 优点

- 初期实现直观
- 小规模场景下代码容易开始写
- “每种实现一个类”符合常见 OO 直觉

### 缺点

- 公共状态容易在父类和子类之间来回拉扯
- 不同子类会重复实现相似生命周期
- 随着实现增多，父类会积累越来越多 hook 或模板方法
- 调度和持久化层虽然不一定马上变复杂，但阅读成本会上升

### 什么时候适合

- 类型数量很少，而且长期稳定
- 差异是“对象身份差异”
- 公共状态不多
- 每个子类都真的是一种完整而稳定的对象类型

## 方案 B：Agent + Driver

示例：

```python
class Agent:
    driver: AgentDriver

class AgentDriver: ...
class NativeAgentDriver(AgentDriver): ...
class ClaudeSdkAgentDriver(AgentDriver): ...
class GeminiCliAgentDriver(AgentDriver): ...
```

### 优点

- 公共状态和执行策略清晰分离
- 新增一种执行方式时，只需新增一个 driver
- `scheduler_service`、`room_service`、持久化逻辑都可以保持无感
- 更符合“策略模式”的问题结构

### 缺点

- 初期比子类方案多一层抽象
- 命名不清晰时，团队需要花一点时间适应
- 如果协议收得不够好，driver 可能会过度依赖 `Agent` 内部字段

### 什么时候适合

- 差异主要是“执行策略差异”
- 公共状态较多，希望统一管理
- 未来接入方式还会继续增加
- 调度、持久化、房间语义要保持稳定

## 为什么当前项目更适合 Driver

### 1. 变化的是执行方式，不是 Agent 身份

`alice` 不会因为底层从 `native` 换成 `claude_sdk` 就变成另一种业务对象。  
她还是同一个聊天室 Agent，只是驱动方式变了。

### 2. `Agent` 已经有很多稳定职责

当前 `Agent` 统一负责：

- 队列消费
- 状态发布
- 历史持久化
- 房间动作语义
- 调度器接入

这些能力不应该因为底层接法不同而分散到不同子类里。

### 3. 后续扩展可能是组合式增长

未来增长的不一定只是：

- `NativeAgent`
- `ClaudeSdkAgent`
- `GeminiCliAgent`

也可能是这些维度交叉：

- 是否持久会话
- 是否支持 streaming
- 是否支持 MCP
- 是否接本地 CLI
- 是否带权限控制

这种情况下，继承很容易走向组合爆炸，而 driver 更适合继续拆分策略。

### 4. 调度器天然更适合面向统一 Agent

调度器只需要知道：

- 这是一个 `Agent`
- 它能 `consume_task`
- 它会在某个房间完成一轮

这已经是一个很好的稳定边界，没有必要让 scheduler 感知很多子类。

## 推荐决策

推荐保留当前方向：

- `Agent` 作为稳定实体
- `AgentDriver` 作为执行策略
- `AgentDriverHost` 作为宿主协议
- `AgentDriverConfig` 作为配置归一化结构

并继续在这个方向上演进，而不是改回多子类方案。

## 后续建议

1. 新配置统一使用 `driver.type`
2. 补一个 `GeminiCliAgentDriver` 最小骨架
3. 逐步收紧 `AgentDriverHost`，减少对内部字段的暴露
4. 如果动作种类继续增加，再抽一层动作协议
