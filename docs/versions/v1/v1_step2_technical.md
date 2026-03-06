# V1: 双 Agent 单房间聊天 - 技术文档

---

## 架构设计

### 类图

```
┌──────────────────┐
│      Agent       │
├──────────────────┤
│ - name: str      │
│ - system_prompt  │
│ - model: str     │
├──────────────────┤
│ + generate()     │
└──────────────────┘
         │
         │ uses
         │
┌──────────────────┐
│    ChatRoom      │
├──────────────────┤
│ - name: str      │
│ - messages: []   │
├──────────────────┤
│ + add_message()  │
│ + get_context()  │
└──────────────────┘
```

### 数据流

```
配置文件 → Agent实例 → ChatRoom → asyncio调度 → 日志输出
```

---

## 目录结构

```
agent_team/
├── config/
│   └── agents_v1.json          # V1 配置文件
├── prompts/
│   ├── alice_system.md        # Alice 的性格设定
│   └── bob_system.md          # Bob 的性格设定
├── src/
│   ├── core/
│   │   ├── agent.py           # Agent 类
│   │   └── chat_room.py       # 聊天室类
│   ├── api/
│   │   └── client.py          # API 客户端 (复用现有)
│   └── main.py                # 主程序
└── logs/
    └── chat.log               # 聊天日志
```

---

## 配置文件

### config/agents_v1.json

```json
{
  "agents": [
    {
      "name": "alice",
      "prompt_file": "prompts/alice_system.md",
      "model": "gml-4.7"
    },
    {
      "name": "bob",
      "prompt_file": "prompts/bob_system.md",
      "model": "gml-4.7"
    }
  ],
  "chat_room": {
    "name": "general",
    "initial_topic": "今天天气真不错，我们来聊聊吧！"
  },
  "max_turns": 5
}
```

### prompts/alice_system.md

```markdown
你是 Alice，一位热情开朗的朋友。

性格特点：
- 喜欢主动开启话题
- 关心别人的生活
- 充满好奇心，喜欢提问
- 表达直接但不失礼貌

说话风格：
- 热情、友善
- 经常使用感叹号
- 喜欢分享自己的想法

你正在和 Bob 聊天，请自然地回应他。
```

### prompts/bob_system.md

```markdown
你是 Bob，一位安静内敛的工程师。

性格特点：
- 回答问题比较谨慎
- 逻辑思维强
- 有独特的幽默感
- 不太擅长闲聊

说话风格：
- 简洁、理性
- 偶尔说冷笑话
- 回答问题时喜欢考虑技术角度

你正在和 Alice 聊天，请自然地回应她。
```

---

## 核心代码

### src/core/agent.py

```python
from typing import List, Dict


class Agent:
    """基础 Agent 类"""

    def __init__(self, name: str, system_prompt: str, model: str):
        self.name = name
        self.system_prompt = system_prompt
        self.model = model

    async def generate_response(self, api_client, context: str) -> str:
        """生成回复"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": context}
        ]

        response = await api_client.call_chat_completion(
            model=self.model,
            messages=messages
        )

        return response.choices[0].message.content
```

### src/core/chat_room.py

```python
from dataclasses import dataclass
from typing import List
from datetime import datetime


@dataclass
class Message:
    sender: str
    content: str
    timestamp: str


class ChatRoom:
    """聊天室类"""

    def __init__(self, name: str, initial_topic: str = ""):
        self.name = name
        self.messages: List[Message] = []
        self.initial_topic = initial_topic

    def add_message(self, sender: str, content: str) -> None:
        """添加消息"""
        message = Message(
            sender=sender,
            content=content,
            timestamp=datetime.now().isoformat()
        )
        self.messages.append(message)

    def get_context(self, max_messages: int = 10) -> str:
        """获取最近的对话上下文"""
        recent_messages = self.messages[-max_messages:]
        context_parts = []
        for msg in recent_messages:
            context_parts.append(f"{msg.sender}: {msg.content}")
        return "\n".join(context_parts)

    def format_log(self) -> str:
        """格式化聊天记录"""
        lines = [f"=== {self.name} 聊天记录 ==="]
        for msg in self.messages:
            lines.append(f"[{msg.timestamp}] {msg.sender}: {msg.content}")
        return "\n".join(lines)
```

### src/main.py

```python
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.agent import Agent
from core.chat_room import ChatRoom
from api.client import APIClient


logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    """加载配置"""
    with open("../config/agents_v1.json", "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt(file_path: str) -> str:
    """加载提示词"""
    with open(f"../{file_path}", "r", encoding="utf-8") as f:
        return f.read().strip()


async def main():
    # 加载配置
    config = load_config()

    # 创建聊天室
    chat_room = ChatRoom(
        name=config["chat_room"]["name"],
        initial_topic=config["chat_room"]["initial_topic"]
    )

    # 创建 API 客户端
    api_client = APIClient()

    # 创建 Agents
    agents = []
    for agent_config in config["agents"]:
        prompt = load_prompt(agent_config["prompt_file"])
        agent = Agent(
            name=agent_config["name"],
            system_prompt=prompt,
            model=agent_config["model"]
        )
        agents.append(agent)

    # 添加初始话题
    if chat_room.initial_topic:
        chat_room.add_message("system", chat_room.initial_topic)

    # 轮流对话
    max_turns = config.get("max_turns", 5)
    logger.info(f"开始 {max_turns} 轮对话...")

    for turn in range(1, max_turns + 1):
        logger.info(f"\n--- 第 {turn} 轮 ---")

        for agent in agents:
            # 获取上下文
            context = chat_room.get_context()

            # 生成回复
            try:
                response = await agent.generate_response(
                    api_client=api_client,
                    context=context
                )

                # 添加消息
                chat_room.add_message(agent.name, response)

                logger.info(f"{agent.name}: {response}")
            except Exception as e:
                logger.error(f"{agent.name} 生成回复失败: {e}")
                break

    # 输出完整聊天记录
    logger.info(f"\n{chat_room.format_log()}")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 技术要点

### asyncio 调度

使用 `asyncio.run()` 启动事件循环，所有异步操作使用 `await` 关键字。

### 上下文传递

每次 Agent 生成回复时，将聊天室的最近消息作为上下文传入，确保对话连贯性。

### 消息格式

使用 `Message` dataclass 统一消息格式，包含发送者、内容和时间戳。
