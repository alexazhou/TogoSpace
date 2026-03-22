# V3: 多 Agent 多房间聊天 - 开发任务表

## 任务概览

V3 在 V2 基础上扩展，核心变更为：新增 V3 配置文件、修改 `agentService` 支持按房间分组、修改 `schedulerService` 支持多房间并发、修改 `config_util` 读取新配置、修改 `main.py` 初始化多个房间。`chat_roomService`、`llm_api_service`、`agent_tool_service` 及所有 model/util 模块直接复用，无需修改。

共 5 个任务，按依赖关系排序。

---

## 任务列表

### 任务 1: 创建 V3 配置文件

**描述**: 新增 `agents_v3.json`，定义多个聊天室及其参与者

**依赖**: 无

**文件**: `config/agents_v3.json`（新建）

**子任务**:
- [ ] 创建 `config/agents_v3.json`
- [ ] 顶层 `agents` 数组定义所有 Agent（alice、bob、charlie）
- [ ] `chat_rooms` 数组包含至少 2 个房间，每个房间含 `name`、`agents`、`initial_topic`、`max_turns`
- [ ] 其中至少有 1 个 Agent 同时出现在多个房间中（验证多房间 Agent）
- [ ] 顶层保留 `max_function_calls` 字段

**验收标准**:
- `agents_v3.json` 能被 `json.load` 正确解析
- 包含 2 个及以上聊天室配置
- 至少 1 个 Agent 同时属于多个房间
- 每个房间的 `agents` 字段引用的名称在顶层 `agents` 中均有定义

---

### 任务 2: 修改 config_util.py

**描述**: 将 `load_config` 的读取路径从 `agents_v2.json` 改为 `agents_v3.json`

**依赖**: 任务 1

**文件**: `src/util/config_util.py`（修改）

**子任务**:
- [ ] 将 `load_config` 中的路径 `../../config/agents_v2.json` 改为 `../../config/agents_v3.json`

**验收标准**:
- `load_config()` 返回 V3 配置结构（含 `chat_rooms` 数组）

---

### 任务 3: 修改 agentService.py

**描述**: 重构 `agentService` 的初始化逻辑，支持按房间分组创建 Agent 实例

**依赖**: 无（接口变更，main.py 依赖此任务）

**文件**: `src/service/agentService.py`（修改）

**子任务**:
- [ ] 将模块级存储从 `_agents: List[Agent]` 改为 `_agents_by_room: Dict[str, List[Agent]]`
- [ ] 修改 `init` 签名：`init(agents_config: list, rooms_config: list) -> None`
  - 构建全局 Agent 定义索引 `{name: cfg}`
  - 遍历 `rooms_config`，为每个房间独立创建 Agent 实例
  - 各房间的 `{participants}` 只包含该房间内的其他成员
- [ ] 修改 `get_agents` 签名：`get_agents(room_name: str) -> List[Agent]`，按房间返回
- [ ] 修改 `close`：清空 `_agents_by_room`

**验收标准**:
- `init` 调用后，`get_agents("general")` 返回 general 房间的 Agent 列表
- 同一 Agent（如 bob）在不同房间是独立实例，`system_prompt` 中的参与者列表不同
- `close()` 后 `get_agents` 返回空列表

---

### 任务 4: 修改 schedulerService.py 和 main.py

**描述**: 调度器支持多房间并发运行；main.py 初始化多个聊天室

**依赖**: 任务 1、任务 2、任务 3

**文件**: `src/service/schedulerService.py`（修改）、`src/main.py`（修改）

**schedulerService.py 子任务**:
- [ ] 将 `_room_name: str` 和 `_max_turns: int` 替换为 `_rooms_config: list`
- [ ] 修改 `init` 签名：`init(rooms_config: list, max_function_calls: int = 5) -> None`
- [ ] 修改 `run`：使用 `asyncio.gather` 并发执行每个房间的 `_run_room` 协程
- [ ] 新增 `_run_room(room_name: str, max_turns: int)` 协程，逻辑与 V2 `run` 相同，但日志中加入房间名前缀 `[room_name]`
- [ ] `_run_room` 每轮调用 `chat_room.get_context_messages(room_name)` 获取当前房间历史，格式为 OpenAI 兼容的全 `role=user` 列表
- [ ] Agent 回复写回房间使用 `chat_room.add_message`，Agent 实例本身不保存任何历史
- [ ] 修改 `stop`：重置 `_rooms_config` 为空列表

**main.py 子任务**:
- [ ] 将日志文件前缀改为 `v3_chat_`
- [ ] 从 `config["chat_rooms"]` 获取房间列表
- [ ] 遍历 `rooms_config`，调用 `chat_room.init` 创建每个房间
- [ ] 调用 `agentService.init(config["agents"], rooms_config)` 传入两个参数
- [ ] 调用 `scheduler.init(rooms_config=rooms_config, ...)` 传入房间配置列表（去掉 `room_name` 和 `max_turns`）
- [ ] 遍历 `rooms_config` 为每个房间添加初始话题
- [ ] `finally` 块中保持完整的清理逻辑（同 V2）

**验收标准**:
- `scheduler.run()` 同时推进多个房间的对话
- 各房间日志中均有 `[room_name]` 前缀
- 程序启动后能看到多个房间的首轮对话几乎同时开始

---

### 任务 5: 集成测试

**描述**: 端到端运行 V3，验证多房间并发对话效果

**依赖**: 任务 1、2、3、4

**子任务**:
- [ ] 运行 `python src/main.py`，确认无报错
- [ ] 验证控制台同时出现多个房间的日志，房间前缀正确
- [ ] 验证 general 房间的参与者感知（alice 的 prompt 中含 bob 和 charlie）
- [ ] 验证 tech 房间的参与者感知（bob 的 prompt 中只含 charlie，不含 alice）
- [ ] 验证同一 Agent（bob）在两个房间中感知到的参与者列表不同
- [ ] 验证各房间消息不串频（general 的消息不出现在 tech 的上下文中）
- [ ] 验证传给 Agent 的消息列表中，历史发言均为 `role=user`，发言者名称内嵌在 content 中
- [ ] 验证日志文件生成在 `logs/` 目录，前缀为 `v3_chat_`

**验收标准**:
- 多个聊天室并发运行，互不干扰
- 每个 Agent 在各自房间中表现出正确的性格特征
- 参与者感知在各房间独立正确
- 程序正常退出（退出码 0）
- 日志文件包含所有房间的完整聊天记录

---

## 任务依赖关系图

```
任务 1 (V3 配置文件)
    │
    └─ 任务 2 (config_util)
                │
任务 3 (agentService) ──┐
                         └─ 任务 4 (scheduler + main)
                                    │
                                    └─ 任务 5 (集成测试)
```

## 开发顺序建议

**推荐顺序**: 任务 1 → 任务 2 + 任务 3（并行）→ 任务 4 → 任务 5

**并行开发机会**:
- 任务 1 和任务 3 没有依赖关系，可以并行开发
- 任务 2 和任务 3 没有依赖关系，可以并行开发

---

## 复用说明

以下 V2 文件**无需修改**，直接复用：

| 文件 | 说明 |
|------|------|
| `src/service/chat_roomService.py` | 已基于字典存储，天然支持多房间 |
| `src/service/llm_api_service.py` | 无状态 HTTP 客户端，与房间概念无关 |
| `src/service/agent_tool_service.py` | 工具加载与执行，与房间概念无关 |
| `src/model/api_model.py` | 纯数据定义 |
| `src/model/coreModel/gtCoreChatModel.py` | 纯数据定义 |
| `src/util/toolLoader_util.py` | 工具元数据加载 |
| `src/util/tool_util.py` | 工具函数注册表 |
| `resource/prompts/alice_system.md` | 直接复用 |
| `resource/prompts/bob_system.md` | 直接复用 |
| `resource/prompts/charlie_system.md` | 直接复用 |

以下文件需要新建或修改：

| 文件 | 操作 | 说明 |
|------|------|------|
| `config/agents_v3.json` | 新建 | 多房间配置结构 |
| `src/util/config_util.py` | 修改 | 读取路径改为 `agents_v3.json` |
| `src/service/agentService.py` | 修改 | 按房间分组存储和初始化 |
| `src/service/schedulerService.py` | 修改 | `asyncio.gather` 并发多房间 |
| `src/main.py` | 修改 | 初始化多个房间，调整 init 调用参数 |

---

## 测试检查清单

- [ ] `agents_v3.json` 格式正确，包含 2 个及以上聊天室
- [ ] `load_config()` 返回含 `chat_rooms` 字段的字典
- [ ] `agentService.get_agents("general")` 返回正确的 Agent 列表
- [ ] `agentService.get_agents("tech")` 返回正确的 Agent 列表
- [ ] bob 在 general 房间的 prompt 中含 alice 和 charlie
- [ ] bob 在 tech 房间的 prompt 中只含 charlie（不含 alice）
- [ ] 多个房间并发运行，日志交替出现
- [ ] 各房间消息独立，不串频
- [ ] 传给 Agent 的 context_messages 中所有历史发言均为 `role=user`（OpenAI 格式）
- [ ] bob 在 general 房间的上下文中不包含 tech 房间的消息（上下文隔离）
- [ ] 日志文件前缀为 `v3_chat_`
- [ ] 程序能正常退出

---

## 验收标准（最终）

- [ ] 支持从配置动态加载多个聊天室
- [ ] Agent 可以同时加入多个聊天室，各房间独立感知参与者
- [ ] 多个房间并发运行，消息路由正确
- [ ] 所有 Agent 在各自房间中表现出配置的性格特征
- [ ] 程序能正常退出
