# Logger 规范

## 1. 在文件顶部声明 logger

每个文件在 import 块之后、业务代码之前声明模块级 logger，不在调用处临时获取。

```python
# ✅ 正确
import logging

logger = logging.getLogger(__name__)

def some_func():
    logger.info("...")
```

```python
# ❌ 错误：在调用处临时获取
def some_func():
    logging.getLogger(__name__).info("...")
```

## 2. 不使用 root logger

使用 `logger.xxx()` 而非 `logging.xxx()`，避免日志来源显示为 `root`。

```python
# ✅ 正确
logger.info("加载工具函数: name=get_weather")

# ❌ 错误：使用 root logger
logging.info("加载工具函数: name=get_weather")
```

## 3. 描述开头，变量放后面

日志以描述性文字开头，变量统一以 `key=value` 形式附在后面，便于搜索和解析。

```python
# ✅ 正确
logger.info(f"创建 Agent: name={name}, model={model}")
logger.info(f"发送消息: sender={sender}, room={room}, msg={msg}")
logger.error(f"生成回复失败: agent={agent}, room={room}, error={e}")

# ❌ 错误：变量开头
logger.info(f"[{name}] 创建 Agent，model={model}")
logger.info(f"[{room}] {agent} 生成回复失败: {e}")
```

## 4. 默认使用 f-string

本项目日志默认使用 f-string，不使用 `%s` 占位风格。

保持一条日志在视觉上完整可读，比套用通用 logging 占位习惯更重要。

```python
# ✅ 正确：统一使用 f-string
logger.info(f"开始执行任务: {agent_name}(agent_id={agent_id}), task_id={task_id}")
logger.warning(f"finish_turn 被房间拒绝: agent={agent_name}, room={room_name}, current_turn={current_name}")

# ❌ 不推荐：使用 %s 占位
logger.info("开始执行任务: %s(agent_id=%s), task_id=%s", agent_name, agent_id, task_id)
```

## 5. 日志优先保持单行

日志调用默认优先单行，便于扫读，也更符合当前仓库的大多数写法。

只有在单行明显过长、已经影响阅读时，才允许拆成多行；不要把本来能一眼看完的日志机械地拆成多行。

```python
# ✅ 正确：能单行看清时保持单行
logger.info(f"首个未完成任务状态不可消费，退出消费循环: {agent_name}(agent_id={agent_id}), task_id={task_id}, task_status={task_status}")

# ❌ 不推荐：无明显必要却拆成多行
logger.info(
    f"首个未完成任务状态不可消费，退出消费循环: {agent_name}(agent_id={agent_id}), "
    f"task_id={task_id}, task_status={task_status}"
)
```

## 6. 日志级别使用规范

| 级别 | 适用场景 |
|------|----------|
| `INFO` | 正常流程节点，如创建对象、状态变更、关键操作 |
| `WARNING` | 非预期但可恢复的情况，如上下文缺失、重试 |
| `ERROR` | 操作失败、异常捕获 |
