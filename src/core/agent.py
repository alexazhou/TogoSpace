from typing import Dict


class Agent:
    """基础 Agent 类"""

    def __init__(self, name: str, system_prompt: str, model: str):
        self.name = name
        self.system_prompt = system_prompt
        self.model = model

    async def generate_response(self, api_client, context_messages: list) -> str:
        """生成回复"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            *context_messages
        ]

        response = await api_client.call_chat_completion(
            model=self.model,
            messages=messages
        )

        return response.choices[0].message.content
