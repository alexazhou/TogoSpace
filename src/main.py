import json
import logging
import os
import requests
from model import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ErrorResponse,
    Message,
    Tool
)
from function_loader import build_tools, execute_function

logging.basicConfig(
    format="%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S"
)


# ========== 配置加载 ==========

def load_config() -> dict:
    with open("../config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    return config["anthropic"]


def load_system_prompt() -> str:
    with open("../resource/system.md", "r", encoding="utf-8") as f:
        return f.read().strip()


def load_user_message() -> str:
    with open("../resource/message.md", "r", encoding="utf-8") as f:
        return f.read().strip()


# ========== API 调用 ==========

def call_chat_completion(request: ChatCompletionRequest, api_key: str, debug: bool = False) -> ChatCompletionResponse:
    """调用 DashScope Chat Completion API"""
    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = request.model_dump(exclude_none=True)

    if debug:
        logging.info("=== 请求 payload ===")
        logging.info(json.dumps(payload, indent=2, ensure_ascii=False))

    response = requests.post(url, headers=headers, json=payload)
    response_data = response.json()

    if debug:
        logging.info("=== API 响应数据 ===")
        logging.info(json.dumps(response_data, indent=2, ensure_ascii=False))

    if response.status_code == 200:
        return ChatCompletionResponse.model_validate(response_data)
    else:
        error = ErrorResponse.model_validate(response_data)
        raise RuntimeError(f"API 调用失败: {error.code} - {error.message}")


# ========== 主程序 ==========

def main():
    # 切换到脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    config = load_config()
    system_prompt = load_system_prompt()
    user_message = load_user_message()

    # 动态加载工具
    tools = build_tools()

    # 构建请求对象
    request = ChatCompletionRequest(
        model=config.get("model", "gml-4.7"),
        messages=[
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_message)
        ],
        tools=tools,
        max_tokens=1024
    )

    try:
        # 调用 API
        response = call_chat_completion(request, config["api_key"], debug=False)

        assistant_message = response.choices[0].message
        logging.info(f"Finish Reason: {response.choices[0].finish_reason}")

        # 检查是否有 tool_calls
        if assistant_message.tool_calls:
            logging.info("检测到工具调用:")
            for tool_call in assistant_message.tool_calls:
                logging.info(f"  Tool ID: {tool_call.id}")
                logging.info(f"  Function: {tool_call.function['name']}")
                logging.info(f"  Arguments: {tool_call.function['arguments']}")

                # 解析参数并调用函数
                args = json.loads(tool_call.function['arguments'])
                result = execute_function(tool_call.function['name'], args)
                logging.info(f"  函数执行结果: {result}")

                # 将工具调用结果添加到消息历史
                messages = [
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=user_message),
                    Message(role="assistant", content=assistant_message.content, tool_calls=assistant_message.tool_calls),
                    Message(role="tool", content=result, tool_call_id=tool_call.id)
                ]

                # 再次调用 API 获取最终响应
                followup_request = ChatCompletionRequest(
                    model=config.get("model", "gml-4.7"),
                    messages=messages,
                    tools=tools,
                    max_tokens=1024
                )
                followup_response = call_chat_completion(followup_request, config["api_key"])
                logging.info(f"最终响应: {followup_response.choices[0].message.content}")
        else:
            logging.info(f"直接响应: {assistant_message.content}")

        logging.info(f"输入 tokens: {response.usage.input_tokens}")
        logging.info(f"输出 tokens: {response.usage.output_tokens}")
        logging.info(f"总 tokens: {response.usage.total_tokens}")
        logging.info(f"请求 ID: {response.request_id}")

    except RuntimeError as e:
        logging.error(f"错误: {e}")
    except Exception as e:
        logging.error(f"未知错误: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
