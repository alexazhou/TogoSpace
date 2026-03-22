# V3: 多 Agent 多房间聊天 - 技术文档

---

## 架构设计

### V2 vs V3 架构对比

V2 中 `schedulerService` 持有单个房间名，只能调度一个聊天室。V3 将调度器改为支持多房间并发：`schedulerService.run()` 为每个房间创建独立的 asyncio Task，并行推进各房间的对话轮次。`agentService` 新增按房间维度检索 Agent 的能力，`chat_roomService` 已天然支持多房间（基于字典存储），无需修改。

同名 Agent 在多个房间中**各自是独立的对象实例**，拥有独立的 `system_prompt`（参与者列表仅限本房间）。`_run_room` 每轮通过 `chat_room.get_context_messages(room_name)` 获取上下文，上下文严格限定在该房间的消息历史中，不会跨房间读取。

### 核心变更点

| 模块 | V2 | V3 |
|------|----|----|
| `config/agents_v3.json` | 单 `chat_room` 对象 | `chat_rooms` 数组，每项含 `agents` 子列表 |
| `util/config_util.py` | 固定读取 `agents_v2.json` | 读取 `agents_v3.json` |
| `service/agentService.py` | 单一 Agent 列表，统一注入 `{participants}` | 按房间维度初始化，各房间独立注入参与者 |
| `service/schedulerService.py` | 单房间顺序轮询 | 多房间并发（`asyncio.gather`） |
| `main.py` | 初始化单个房间 + 单个 scheduler | 初始化多个房间，scheduler 并发运行 |

### 类图

```
┌──────────────────────────┐
│       agentService      │
├──────────────────────────┤
│ _agents: Dict[room, list]│  # 按房间分组的 Agent 实例
│                          │  # 同名 Agent 在不同房间是独立对象
├──────────────────────────┤
│ init(rooms_config)       │
│ get_agents(room_name)    │
│ close()                  │
└──────────────────────────┘
          │ uses
          ▼
┌──────────────────────────┐        ┌──────────────────────────────┐
│    chat_roomService     │        │      schedulerService       │
├──────────────────────────┤        ├──────────────────────────────┤
│ _rooms: Dict[str, room]  │◄───────│ _rooms: List[RoomConfig]     │
│ (复用 V2，无需修改)       │        │ _max_function_calls: int     │
├──────────────────────────┤        ├──────────────────────────────┤
│ init() / add_message()   │        │ init(rooms_config, ...)      │
│ get_context_messages()   │        │ run()  # asyncio.gather      │
│ format_log()             │        │ _run_room(room_name, turns)  │
└──────────────────────────┘        │ stop()                       │
                                    └──────────────────────────────┘
```

### 数据流

```
配置文件 → 多个房间配置 → 多组 Agent 实例 → Scheduler（并发）→ 各 ChatRoom → 日志输出
```

---

## 目录结构

```
agent_team/
├── config/
│   ├── agents_v1.json            # V1 配置（不变）
│   ├── agents_v2.json            # V2 配置（不变）
│   └── agents_v3.json            # V3 配置（新增）
├── resource/
│   └── prompts/
│       ├── alice_system.md       # 复用 V2
│       ├── bob_system.md         # 复用 V2
│       └── charlie_system.md     # 复用 V2
├── src/
│   ├── model/
│   │   ├── api_model.py          # 复用 V2（不变）
│   │   └── chat_model.py         # 复用 V2（不变）
│   ├── service/
│   │   ├── agentService.py      # 修改：按房间分组初始化
│   │   ├── agent_tool_service.py # 复用 V2（不变）
│   │   ├── chat_roomService.py  # 复用 V2（不变）
│   │   ├── llm_api_service.py    # 复用 V2（不变）
│   │   └── schedulerService.py  # 修改：支持多房间并发
│   ├── util/
│   │   ├── config_util.py        # 修改：读取 agents_v3.json
│   │   ├── toolLoader_util.py   # 复用 V2（不变）
│   │   └── tool_util.py          # 复用 V2（不变）
│   └── main.py                   # 修改：初始化多个房间
└── logs/
    └── v3_chat_<timestamp>.log   # V3 日志文件
```

---

## 配置文件

### config/agents_v3.json

```json
{
  "agents": [
    {
      "name": "alice",
      "prompt_file": "resource/prompts/alice_system.md",
      "model": "qwen-flash"
    },
    {
      "name": "bob",
      "prompt_file": "resource/prompts/bob_system.md",
      "model": "qwen-flash"
    },
    {
      "name": "charlie",
      "prompt_file": "resource/prompts/charlie_system.md",
      "model": "qwen-flash"
    }
  ],
  "chat_rooms": [
    {
      "name": "general",
      "agents": ["alice", "bob", "charlie"],
      "initial_topic": "大家好，今天我们来聊聊生活和工作吧！",
      "max_turns": 6
    },
    {
      "name": "tech",
      "agents": ["bob", "charlie"],
      "initial_topic": "今天我们聊聊技术话题吧！",
      "max_turns": 4
    }
  ],
  "max_function_calls": 5
}
```

**结构说明**：
- 顶层 `agents` 定义所有可用 Agent 及其模型和提示词路径
- `chat_rooms` 数组定义各聊天室，每个房间通过 `agents` 字段引用参与者（按名字）
- 各房间有独立的 `initial_topic` 和 `max_turns`

---

## 技术要点

### 调度逻辑

**房间级调度（并发）**：`run()` 通过 `asyncio.gather` 为每个房间启动一个独立的 `_run_room` 协程，所有房间同时推进，互不等待。

```
run()
 ├── _run_room("general", max_turns=6)  ─┐
 └── _run_room("tech",    max_turns=4)  ─┴─ asyncio.gather 并发执行
```

**房间内 Agent 调度（顺序轮询）**：`_run_room` 内部用 `(turn - 1) % len(agents)` 循环选取当前发言的 Agent，每轮只有一个 Agent 发言，等其回复写回房间后再进入下一轮。

```
_run_room("general", agents=[alice, bob, charlie], max_turns=6)

第1轮 → alice  发言 → 写入 general
第2轮 → bob    发言 → 写入 general
第3轮 → charlie发言 → 写入 general
第4轮 → alice  发言 → 写入 general
...
```

**两个房间并发时的实际执行顺序示例**：

```
general: alice  发言（等待 LLM）
tech:    bob    发言（等待 LLM）  ← general 等待期间，tech 同步推进
general: alice  回复写入
tech:    bob    回复写入
general: bob    发言（等待 LLM）
tech:    charlie发言（等待 LLM）
...
```

各房间的 LLM 调用均为 `await`，IO 等待期间 asyncio 事件循环会切换到其他房间的协程继续执行，实现交替推进。

### 历史消息格式与 API 兼容性

`get_context_messages` 将房间历史转换为如下格式传给 Agent：

```json
[
  {"role": "system", "content": "大家好，今天我们来聊聊生活和工作吧！"},
  {"role": "user",   "content": "alice: 大家好！今天天气真棒！"},
  {"role": "user",   "content": "bob: 嗨，最近在修一个 bug。"},
  {"role": "user",   "content": "charlie: 有趣，修 bug 和苏格拉底的自我认知有几分相似。"}
]
```

所有历史发言（包括当前 Agent 自己之前的发言）统一使用 `role=user`，发言者名称以 `"sender: content"` 格式内嵌在 content 中。

**消息构建方案**：

不同 API 对消息格式的要求不同，需分别处理。当前使用 OpenAI 格式，Anthropic 格式待后续支持。

**OpenAI / DashScope（当前使用）**：允许连续多条 `role=user` 消息，所有历史发言（包括当前 Agent 自己的）统一设为 `role=user`，发言者名称内嵌在 content 中。

以 charlie 视角为例：

```json
[
  {"role": "system", "content": "大家好，今天我们来聊聊生活和工作吧！"},
  {"role": "user",   "content": "alice: 大家好！"},
  {"role": "user",   "content": "bob: 嗨，最近在修一个 bug。"},
  {"role": "user",   "content": "charlie: 修 bug 和苏格拉底有几分相似。"},
  {"role": "user",   "content": "alice: 哈哈，这个比喻太妙了！"},
  {"role": "user",   "content": "bob: 我更愿意把它比作找漏气的轮胎。"}
]
```

**Anthropic（暂不支持）**：要求 `user` / `assistant` 严格交替，连续多条 `role=user` 会报错。需将当前 Agent 自己的历史发言设为 `role=assistant`，连续的其他人发言打包进单条 `role=user` 消息的 `content` 数组中，每人一个 text block，自然形成交替结构。

以 charlie 视角为例：

```json
[
  {
    "role": "user",
    "content": [
      {"type": "text", "text": "alice: 大家好！"},
      {"type": "text", "text": "bob: 嗨，最近在修一个 bug。"}
    ]
  },
  {
    "role": "assistant",
    "content": "修 bug 和苏格拉底有几分相似。"
  },
  {
    "role": "user",
    "content": [
      {"type": "text", "text": "alice: 哈哈，这个比喻太妙了！"},
      {"type": "text", "text": "bob: 我更愿意把它比作找漏气的轮胎。"}
    ]
  }
]
```

边界情况：若历史第一条消息恰好是当前 Agent 自己发的，转换后首条为 `role=assistant`，违反 Anthropic"第一条必须是 user"的要求，需在前面插入一条占位 `user` 消息处理。

### Agent 无状态与消息历史管理

`Agent` 类本身不持有任何消息历史，`name`、`system_prompt`、`model` 是其全部状态。消息历史统一存储在 `ChatRoom.messages` 中。

每轮发言的流程：
1. `scheduler` 调用 `chat_room.get_context_messages(room_name)` 从房间取出历史
2. 将历史作为 `context_messages` 参数注入 `Agent.generate_with_function_calling`
3. Agent 生成回复后，`scheduler` 调用 `chat_room.add_message` 将回复写回房间

这一设计是 V3 同名 Agent 能在多个房间独立运行的基础——Agent 自身不持有历史，上下文完全由房间提供和隔离，不同房间的 Agent 实例之间不存在任何共享状态。

### 同名 Agent 的实例隔离与上下文隔离

同名 Agent（如 bob）在多个房间中是完全独立的对象实例，隔离体现在两个层面：

**实例隔离**：`agentService.init` 为每个房间独立调用 `Agent(...)` 构造，bob 在 general 房间和 tech 房间分别持有一个实例。两个实例的 `system_prompt` 不同——`{participants}` 占位符只替换为该房间内的其他成员（general 中为 alice 和 charlie，tech 中只有 charlie）。

**上下文隔离**：`_run_room` 每轮通过 `chat_room.get_context_messages(room_name)` 获取消息历史，该接口只返回指定房间的消息。bob 在 tech 房间发言时，拿到的上下文仅包含 tech 房间的历史，完全感知不到 general 房间发生的对话。

### 配置结构调整

V3 将 V2 的单 `chat_room` 对象改为 `chat_rooms` 数组，`max_turns` 下沉到每个房间配置，允许各房间独立设置对话轮次。全局 `max_function_calls` 保持在顶层。

### 复用 V2 核心组件

`chat_roomService` 内部已使用字典 `_rooms` 存储多个房间实例，V3 无需任何修改即可支持多房间场景。`llm_api_service`、`agent_tool_service` 和所有 model/util 模块同样直接复用。

---

## 接口定义

### service/agentService.py（修改）

```python
# 按房间名分组存储：{room_name: [Agent, ...]}
# 同名 Agent 在不同房间是独立对象实例
_agents_by_room: Dict[str, List[Agent]] = {}

def init(agents_config: list, rooms_config: list) -> None:
    """根据全局 Agent 定义和各房间配置初始化 Agent 实例。
    每个房间独立创建 Agent 实例，各房间的 {participants} 只包含该房间内的其他成员。
    """

def get_agents(room_name: str) -> List[Agent]:
    """返回指定房间的 Agent 列表。"""

def close() -> None:
    """清空所有 Agent，程序退出前调用。"""
```

### service/schedulerService.py（修改）

```python
def init(rooms_config: list, max_function_calls: int = 5) -> None:
    """初始化调度器，须在 run() 前调用一次。"""

def stop() -> None:
    """重置调度器状态。"""

async def run() -> None:
    """使用 asyncio.gather 并发运行所有房间的对话。"""

async def _run_room(room_name: str, max_turns: int) -> None:
    """运行单个房间的调度循环，逻辑与 V2 run() 相同，日志加 [room_name] 前缀。"""
```

### util/config_util.py（修改）

```python
def load_config() -> dict:
    """读取 config/agents_v3.json。"""
```

---

## 修改的模块

| 模块 | 变更 | 依赖的项目内模块 |
|------|------|----------------|
| `util/config_util.py` | 读取路径改为 `agents_v3.json` | 无 |
| `service/agentService.py` | `init` 签名新增 `rooms_config`；内部按房间分组存储 | `util.config_util`（load_prompt）<br>`service.llm_api_service` |
| `service/schedulerService.py` | `init` 接收 `rooms_config` 列表；`run` 用 `asyncio.gather` 并发；新增 `_run_room` | `service.agentService`<br>`service.chat_roomService`<br>`service.agent_tool_service` |
| `main.py` | 遍历 `chat_rooms` 初始化；传 `rooms_config` 给 `scheduler.init` | 同 V2，不变 |


