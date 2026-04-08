# 通用编码规范

## 1. 显式且精确的逻辑判断

在进行条件判断时，优先使用显式的比较操作，明确区分 `None`、空列表、`0` 或 `False`。避免过度依赖隐式的布尔值判断（truthiness），除非业务逻辑确实需要涵盖所有“虚值”情况。

- **判断列表/集合为空**：优先使用 `len(items) == 0`。
- **判断对象是否存在**：优先使用 `is not None` 或 `is None`。

```python
# 推荐：显式判断长度
if len(members) == 0:
    return

# 推荐：显式判断 None
if agent_id is not None:
    ...

# 不推荐：隐式判断（容易混淆空列表、None、0 等情况）
if not members:
    ...
```

这种风格能让意图更精确，减少潜在的逻辑陷阱。

## 2. 方法定义前至少保留一个空行

每个方法定义前，至少保留一个空行。

- 不要和上一个方法连在一起
- 不要和类定义连在一起（包括类中的第一个方法）

```python
class Agent:

    def startup(self):
        ...

    def run_chat_turn(self):
        ...
```

## 3. `if` 分支前保留一个空行

当 `if` 前面已经有一段赋值、调用或状态准备逻辑时，在 `if` 之前加一个空行，让分支入口更醒目。

```python
target_room = roomService.get_room(room_key)

if target_room is None:
    ...
```

适用场景：

- 变量准备完后进入分支判断
- 一段副作用调用后进入条件判断
- 多个平级 `if` 分支之间

## 4. `return` 后保留一个空行

如果 `return` 后面还有同级分支或后续逻辑，`return` 后应空一行，避免视觉上挤在一起。

```python
if current_room is None:
    return result

return fallback
```

## 5. 连续分支之间留空行

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

## 6. 简单代码不强行加空行

这份规范的目标是增强可读性，不是制造无意义的空白。

下面这种很短、很直接的逻辑，不需要机械地每行都加空行：

```python
if not tool_calls:
    return None
```

## 7. 优先服务于阅读节奏

当你不确定是否该加空行时，用这个判断标准：

- 这段代码是否在“准备状态”和“进入判断”之间切换
- 这段代码是否在“返回/结束当前分支”和“继续后续逻辑”之间切换
- 加上空行后，是否更容易一眼看出代码结构

如果答案是”是”，就加空行。

## 8. 参数较少时优先单行

函数调用或构造函数参数较少（通常 1-2 个）时，优先写在一行，保持紧凑。只有参数过多或复杂时才换行。

```python
# 参数少，单行更紧凑
message = LlmApiMessage(role=OpenaiLLMApiRole.USER, content=f"{room.name} 房间系统消息: {msg.content}")

# 参数多，换行更清晰
agent = Agent(
    name=name,
    team_name=team_name,
    system_prompt=full_prompt,
    model=cfg["model"],
    driver_config=driver_config,
)
```

判断标准：一眼能看清所有参数，无需滚动或脑补，就保持单行。

## 9. 修改现有代码时优先使用紧凑排版风格

当你是在已有代码基础上做修改时，优先使用紧凑排版风格，不要机械地套用“每个参数单独一行”这类展开式写法。

尤其是下面这类语句：

- `logger.xxx(...)`
- `assert ...`
- 参数不多的函数调用
- 参数很多但仍能在 1-2 行内清晰读完的调用

不要因为新增了一两个参数，就把原本可以紧凑表达的调用强行改成每行一个参数。

```python
# 推荐：优先保持紧凑排版
await agentActivityService.add_activity(
    gt_agent=self.gt_agent, activity_type=AgentActivityType.AGENT_STATE,
    status=AgentActivityStatus.SUCCEEDED, detail=status.name, error_message=error_message,
)

# 不推荐：参数并不复杂，却机械地每行拆一个
await agentActivityService.add_activity(
    gt_agent=self.gt_agent,
    activity_type=AgentActivityType.AGENT_STATE,
    status=AgentActivityStatus.SUCCEEDED,
    detail=status.name,
    error_message=error_message,
)
```

判断标准不是“能不能拆”，而是“拆开之后是否真的更清晰”。如果没有明显提升可读性，就保持紧凑。

## 10. 日志和 assert 优先单行

`logger.info(...)`、`logger.warning(...)`、`logger.error(...)` 以及 `assert ...` 这类语句，默认优先写成单行，保持紧凑，便于快速扫读。

只有在单行明显过长时才换行。建议阈值为 100 个字符左右；未超过时不要为了形式刻意拆行。

```python
# 推荐：能单行看清时保持单行
assert room is not None, f"room 不存在: room_id={room_id}"
logger.warning(f"检测到 SDK Agent 直接输出文字: agent={agent.key}, text={text[:50]!r}")

# 推荐：明显过长时再换行
assert some_really_long_condition, (
    f"这里的错误消息很长，单行会明显影响阅读，因此允许换行"
)
```

## 11. 方法内局部变量的类型注解按“是否一眼能看出来”决定

方法内部的局部变量，不需要机械地全部加类型注解。

判断标准：

- 如果变量来自某个方法调用，而返回类型从调用点一眼看不出来，应该加类型注解
- 如果变量类型从右侧表达式一眼就能确定，就不要加，避免噪音

```python
# 推荐：调用点看不出返回类型，补注解
assistant_message: llmApiUtil.OpenAIMessage = await self._infer_to_item(output_item, tools)
infer_result: llmService.InferResult = await llmService.infer(self.model, ctx)

# 不推荐：右侧一眼就知道类型，不需要补注解
result = json.dumps(result_data, ensure_ascii=False)
max_retries = max(1, turn_setup.max_retries)
```

核心原则不是“尽量多写类型”，而是“只在能明显提升阅读效率时写类型”。
