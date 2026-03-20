# 代码排版规范

## 目标

这份规范只关注可读性相关的空行和结构排版，帮助代码在阅读时保持稳定节奏。

## 1. 方法前保留一个空行

类中的每个方法定义前，保留一个空行，不要紧贴上一段逻辑。

```python
class Agent:
    def startup(self):
        ...

    def run_chat_turn(self):
        ...
```

## 2. `if` 分支前保留一个空行

当 `if` 前面已经有一段赋值、调用或状态准备逻辑时，在 `if` 之前加一个空行，让分支入口更醒目。

```python
target_room = room_service.get_room(room_key)

if target_room is None:
    ...
```

适用场景：

- 变量准备完后进入分支判断
- 一段副作用调用后进入条件判断
- 多个平级 `if` 分支之间

## 3. `return` 后保留一个空行

如果 `return` 后面还有同级分支或后续逻辑，`return` 后应空一行，避免视觉上挤在一起。

```python
if current_room is None:
    return result

return fallback
```

## 4. 连续分支之间留空行

同一层级下，多个 `if / elif / else` 分支如果中间夹着较长逻辑，允许通过空行拉开阅读节奏。

```python
if content.startswith(system_prefix):
    ...
    continue

if user_sep in content:
    ...
    continue

prompt_lines.append(content)
```

## 5. 简单代码不强行加空行

这份规范的目标是增强可读性，不是制造无意义的空白。

下面这种很短、很直接的逻辑，不需要机械地每行都加空行：

```python
if not tool_calls:
    return None
```

## 6. 优先服务于阅读节奏

当你不确定是否该加空行时，用这个判断标准：

- 这段代码是否在“准备状态”和“进入判断”之间切换
- 这段代码是否在“返回/结束当前分支”和“继续后续逻辑”之间切换
- 加上空行后，是否更容易一眼看出代码结构

如果答案是”是”，就加空行。

## 7. 参数较少时优先单行

函数调用或构造函数参数较少（通常 1-2 个）时，优先写在一行，保持紧凑。只有参数过多或复杂时才换行。

```python
# 参数少，单行更紧凑
message = LlmApiMessage(role=OpenaiLLMApiRole.USER, content=f”{room.name} 房间系统消息: {msg.content}”)

# 参数多，换行更清晰
agent = Agent(
    name=name,
    team_name=team_name,
    system_prompt=full_prompt,
    model=cfg[“model”],
    driver_config=driver_config,
)
```

判断标准：一眼能看清所有参数，无需滚动或脑补，就保持单行。
