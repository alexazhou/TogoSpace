from typing import Dict, Any, Optional, List, Tuple
import logging


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

    async def generate_with_function_calling(
        self,
        api_client,
        context_messages: List[dict],
        tools: Optional[List[Dict[str, Any]]] = None,
        function_executor: callable = None,
        max_function_calls: int = 5
    ) -> Tuple[str, List[dict]]:
        """支持 Function Calling 的响应生成

        实现逻辑：
        1. 调用 API 获取响应
        2. 检查是否有 tool_calls
        3. 如果有，执行函数并将结果返回给 LLM
        4. 重复直到没有 tool_calls 或达到最大次数
        5. 返回最终回复和工具调用信息

        Args:
            api_client: API 客户端
            context_messages: 上下文消息列表
            tools: 工具列表
            function_executor: 函数执行器
            max_function_calls: 最大函数调用次数

        Returns:
            (最终回复内容, 工具调用信息列表)
        """
        logger = logging.getLogger(__name__)

        # 初始化消息列表
        messages = [
            {"role": "system", "content": self.system_prompt},
            *context_messages
        ]

        # 工具调用记录
        tool_calls_info = []
        function_call_count = 0

        while function_call_count < max_function_calls:
            # 调用 API
            response = await api_client.call_chat_completion(
                model=self.model,
                messages=messages,
                tools=tools
            )

            # 获取助手消息
            assistant_message = response.choices[0].message
            messages.append({
                "role": "assistant",
                "content": assistant_message.content,
                "tool_calls": assistant_message.tool_calls
            })

            # 检查是否有 tool_calls
            if not assistant_message.tool_calls:
                # 没有工具调用，返回最终回复
                final_content = assistant_message.content or ""
                return final_content, tool_calls_info

            # 处理工具调用
            logger.info(f"[{self.name}] 检测到 {len(assistant_message.tool_calls)} 个工具调用")

            for tool_call in assistant_message.tool_calls:
                function_name = tool_call.function.get("name")
                function_args = tool_call.function.get("arguments", {})
                tool_call_id = tool_call.id

                logger.info(f"[{self.name}] 调用函数: {function_name}, 参数: {function_args}")

                # 执行函数
                if function_executor:
                    try:
                        result = function_executor(function_name, function_args)
                        logger.info(f"[{self.name}] 函数执行结果: {result}")
                    except Exception as e:
                        logger.error(f"[{self.name}] 函数执行失败: {e}")
                        result = f"函数执行失败: {str(e)}"
                else:
                    result = "函数执行器未配置"

                # 记录工具调用信息
                tool_calls_info.append({
                    "function": function_name,
                    "arguments": function_args,
                    "result": result
                })

                # 添加工具响应消息
                messages.append({
                    "role": "tool",
                    "content": result,
                    "tool_call_id": tool_call_id
                })

            function_call_count += 1

        # 达到最大函数调用次数
        logger.warning(f"[{self.name}] 达到最大函数调用次数 {max_function_calls}")
        return assistant_message.content or "", tool_calls_info
