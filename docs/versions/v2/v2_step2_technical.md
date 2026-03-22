# V2: 多 Agent 单房间聊天 - 技术文档

---

## 架构设计

### V1 vs V2 架构对比

V1 中 `main.py` 使用简单的双重 for 循环（轮次 × Agent），调度逻辑完全内联。V2 将调度器抽象为独立的 `Scheduler` 类，支持任意数量的 Agent 循环发言，并在 Agent 的 system prompt 中动态注入参与者信息。

### 类图

```
┌──────────────────────┐
│        Agent         │
├──────────────────────┤
│ - name: str          │
│ - system_prompt: str │
│ - model: str         │
├──────────────────────┤
│ + generate_response()│
│ + generate_with_fc() │
└──────────────────────┘
          │
          │ uses
          ▼
┌──────────────────────┐        ┌─────────────────────────┐
│      ChatRoom        │        │        Scheduler        │
├──────────────────────┤        ├─────────────────────────┤
│ - name: str          │◄───────│ - agents: List[Agent]   │
│ - messages: []       │        │ - chat_room: ChatRoom   │
├──────────────────────┤        │ - max_turns: int        │
│ + add_message()      │        ├─────────────────────────┤
│ + get_context_msgs() │        │ + run()                 │
│ + format_log()       │        └─────────────────────────┘
└──────────────────────┘
```

### 数据流

```
配置文件 → N 个 Agent 实例 → Scheduler → ChatRoom → asyncio 事件循环 → 日志输出
```

---

## 目录结构

```
agent_team/
├── config/
│   ├── agents_v1.json            # V1 配置（保持不变）
│   └── agents_v2.json            # V2 配置（新增）
├── resource/
│   └── prompts/
│       ├── alice_system.md       # 复用 V1
│       ├── bob_system.md         # 复用 V1
│       └── charlie_system.md     # 新增
├── src/
│   ├── core/
│   │   ├── agent.py              # 复用 V1（无需修改）
│   │   ├── chat_room.py          # 复用 V1（无需修改）
│   │   └── scheduler.py          # 新增：调度器
│   ├── utils/
│   │   └── api.py                # 从 api/client.py 移入并改名，作为无状态工具函数
│   ├── main.py                   # 主程序入口（V2 修改，支持多 Agent）
│   └── tools/
│       ├── function_loader.py    # 复用 V1，移入此目录
│       └── functions.py          # 复用 V1，移入此目录
└── logs/
    └── v2_chat_<timestamp>.log   # V2 日志文件
```

---

## 配置文件

### config/agents_v2.json

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
  "chat_room": {
    "name": "general",
    "initial_topic": "大家好，今天我们来聊聊生活和工作吧！"
  },
  "max_turns": 6
}
```

### resource/prompts/charlie_system.md

```markdown
你是 Charlie，一位热爱哲学思辨的大学教授。

性格特点：
- 喜欢将日常话题引申到更深层的意义
- 对人性和社会有独特的洞察
- 善于倾听，但总能找到角度加以评论
- 不喜欢表面化的闲聊

说话风格：
- 语言优雅，偶尔引经据典
- 提问方式发人深省
- 回应中带有哲学色彩

你正在和 {participants} 聊天，请自然地融入对话。
```

---

## 核心代码

### src/core/scheduler.py（新增）

```python
import asyncio
import logging
from typing import List

from core.agent import Agent
from core.chat_room import ChatRoom
from tools.function_loader import build_tools, execute_function
from utils.api import call_chat_completion

logger = logging.getLogger(__name__)


class Scheduler:
    """多 Agent 调度器：按轮次让 Agent 依次发言"""

    def __init__(self, agents: List[Agent], chat_room: ChatRoom, max_turns: int):
        self.agents = agents
        self.chat_room = chat_room
        self.max_turns = max_turns
        self.tools = build_tools()

    async def run(self) -> None:
        """运行调度循环"""
        agent_names = [a.name for a in self.agents]
        logger.info(f"参与者: {agent_names}")
        logger.info(f"开始 {self.max_turns} 轮对话...")

        for turn in range(1, self.max_turns + 1):
            current_agent = self.agents[(turn - 1) % len(self.agents)]
            logger.info(f"\n--- 第 {turn} 轮 ({current_agent.name}) ---")

            context_messages = self.chat_room.get_context_messages()

            try:
                agent_context = {
                    "chat_room": self.chat_room,
                    "agent_name": current_agent.name
                }
                final_response, _ = await current_agent.generate_with_function_calling(
                    api_call=call_chat_completion,
                    context_messages=context_messages,
                    tools=self.tools,
                    function_executor=lambda name, args: execute_function(
                        name, args, context=agent_context
                    ),
                    max_function_calls=1
                )
                if final_response:
                    self.chat_room.add_message(current_agent.name, final_response)
                    logger.info(f"{current_agent.name}: {final_response}")
            except Exception as e:
                logger.error(f"{current_agent.name} 生成回复失败: {e}")
                return

        logger.info(f"\n{self.chat_room.format_log()}")
```

### src/main.py（修改）

```python
import asyncio
import json
import logging
import os
from datetime import datetime

from core.agent import Agent
from core.chat_room import ChatRoom
from core.scheduler import Scheduler


def setup_logger() -> None:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_file = os.path.join(log_dir, f"v2_chat_{timestamp}.log")

    log_format = "%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s"
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    for handler in [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]:
        handler.setFormatter(logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S"))
        root_logger.addHandler(handler)


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "../config/agents_v2.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_api_key() -> str:
    config_path = os.path.join(os.path.dirname(__file__), "../config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)["anthropic"]["api_key"]


def load_prompt(file_path: str) -> str:
    full_path = os.path.join(os.path.dirname(__file__), "../", file_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return f.read().strip()


async def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    setup_logger()
    logger = logging.getLogger(__name__)

    config = load_config()
    api_key = load_api_key()

    # 创建聊天室
    chat_room = ChatRoom(
        name=config["chat_room"]["name"],
        initial_topic=config["chat_room"]["initial_topic"]
    )

    # 创建 Agent 实例（数量由配置决定）
    agent_names = [a["name"] for a in config["agents"]]
    agents = []
    for agent_config in config["agents"]:
        # 将其他参与者注入 prompt
        other_names = [n for n in agent_names if n != agent_config["name"]]
        prompt = load_prompt(agent_config["prompt_file"])
        prompt = prompt.replace("{participants}", "、".join(other_names))
        agents.append(Agent(
            name=agent_config["name"],
            system_prompt=prompt,
            model=agent_config["model"]
        ))

    logger.info(f"已创建 {len(agents)} 个 Agent: {agent_names}")

    # 添加初始话题
    if chat_room.initial_topic:
        chat_room.add_message("system", chat_room.initial_topic)

    scheduler = Scheduler(
        agents=agents,
        chat_room=chat_room,
        max_turns=config.get("max_turns", 6)
    )
    await scheduler.run()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 模块依赖关系

### 三层架构与依赖规则

```
┌─────────────────────────────────────────────────────┐
│                     main.py                         │
│  (程序入口，组装所有层)                               │
└────────────────────┬────────────────────────────────┘
                     │ import
        ┌────────────▼────────────┐
        │       service 层        │  有状态类，处理业务逻辑
        ├─────────────────────────┤
        │  schedulerService.py   │
        │  agentService.py       │
        │  chat_roomService.py   │
        │  api_client_service.py  │
        │  function_service.py    │
        └────────────┬────────────┘
                     │ import
        ┌────────────▼────────────┐
        │        model 层         │  纯数据定义（dataclass / pydantic）
        ├─────────────────────────┤
        │  api_model.py           │
        │  chat_model.py          │
        └────────────┬────────────┘
                     │ import（单向）
        ┌────────────▼────────────┐
        │         util 层         │  无状态工具函数
        ├─────────────────────────┤
        │  config_util.py         │
        │  function_loader_util.py│
        │  functions_util.py      │
        └─────────────────────────┘
```

> 依赖方向严格单向：`main → service → model / util`，禁止下层反向引用上层。

---

### 各模块详细依赖

#### util 层（无外部项目内依赖）

| 模块 | 依赖的项目内模块 | 依赖的第三方库 |
|------|----------------|--------------|
| `config_util.py` | 无 | 标准库（json, os, logging, datetime） |
| `functions_util.py` | 无 | 标准库（typing, datetime, logging） |
| `function_loader_util.py` | `util.functions_util`（FUNCTION_REGISTRY） | 标准库（inspect, json, logging, typing） |

#### model 层

| 模块 | 依赖的项目内模块 | 依赖的第三方库 |
|------|----------------|--------------|
| `chat_model.py` | 无 | 标准库（dataclasses） |
| `api_model.py` | 无 | pydantic |

#### service 层

| 模块 | 依赖的项目内模块 | 依赖的第三方库 |
|------|----------------|--------------|
| `chat_roomService.py` | `model.chat_model`（ChatMessage） | 标准库（typing, datetime） |
| `api_client_service.py` | `model.api_model`（ChatCompletionRequest / Response / ErrorResponse） | aiohttp, certifi |
| `agentService.py` | 无（api_client 以参数注入） | 标准库（typing, logging, json） |
| `function_service.py` | `model.api_model`（Tool, Function, FunctionParameter）<br>`util.function_loader_util`（load_enabled_functions, get_function_metadata）<br>`util.functions_util`（FUNCTION_REGISTRY） | 标准库（logging, typing） |
| `schedulerService.py` | `service.agentService`（Agent）<br>`service.chat_roomService`（ChatRoom）<br>`service.function_service`（build_tools, execute_function） | 标准库（logging, typing） |

#### main.py

| 依赖的项目内模块 |
|----------------|
| `util.config_util`（setup_logger, load_config, load_prompt, load_api_key） |
| `service.agentService`（Agent） |
| `service.chat_roomService`（ChatRoom） |
| `service.schedulerService`（Scheduler） |
| `service.api_client_service`（APIClient） |

---

### 关键依赖路径

```
main.py
  ├── util.config_util              （读取配置/日志）
  ├── service.api_client_service
  │     └── model.api_model         （请求/响应数据结构）
  ├── service.chat_roomService
  │     └── model.chat_model        （ChatMessage dataclass）
  ├── service.agentService         （无内部依赖，api_client 注入）
  └── service.schedulerService
        ├── service.agentService
        ├── service.chat_roomService
        └── service.function_service
              ├── model.api_model   （Tool / Function / FunctionParameter）
              ├── util.function_loader_util
              │     └── util.functions_util
              └── util.functions_util（FUNCTION_REGISTRY）
```

---

## 技术要点

### 动态 Agent 加载

`main.py` 通过遍历配置文件中的 `agents` 数组动态创建实例，不再硬编码 Agent 数量，配置几个就运行几个。

### 参与者感知

创建 Agent 时，将同一聊天室内其他 Agent 的名字替换到 system prompt 的 `{participants}` 占位符中，使每个 Agent 都知道自己在和谁聊天。

### Scheduler 调度

`Scheduler.run()` 使用 `(turn - 1) % len(agents)` 实现循环轮询，与 Agent 数量无关，天然支持 2 个到 N 个 Agent。

### 复用 V1 核心组件

`Agent`、`ChatRoom` 均无需修改直接复用，V2 新增了 `Scheduler` 类和 `src/utils/` 目录（存放 `api.py` 工具函数），将 `function_loader.py` 和 `functions.py` 移入 `src/tools/` 目录，并修改 `main.py` 以支持多 Agent，同时新增配置/提示词文件。
