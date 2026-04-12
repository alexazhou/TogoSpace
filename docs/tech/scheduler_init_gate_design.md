# 未初始化场景下的调度闸门方案

本文档用于讨论以下问题：

- 用户首次使用时，后端已经启动，但尚未配置可用的 LLM
- 当前运行时仍会恢复 Team / Room 并进入调度链路
- 调度继续向下执行后，会在推理阶段因 `current_llm_service` 为空而报错
- 用户完成初始化配置后，页面上仍可能看到之前遗留的失败状态

相关现状参考：

- [docs/versions/v13/v13_step1_product.md](../versions/v13/v13_step1_product.md)
- `src/backend_main.py`
- `src/service/teamService.py`
- `src/service/schedulerService.py`
- `src/service/llmService.py`
- `src/service/agentService/agentTaskConsumer.py`

## 1. 问题定义

当前实现中，“未配置 LLM”虽然不会阻止后端启动，但不会阻止调度启动。

现有链路大致如下：

1. 后端启动
2. 恢复 Team runtime
3. `teamService.restore_team()` 调用 `schedulerService.start_scheduling()`
4. Room 激活后发布调度事件
5. Scheduler 创建 Agent task 并启动 consumer
6. 推理阶段读取 `setting.current_llm_service`
7. 因无可用 LLM 抛错，任务被标记为 `FAILED`，Agent 进入 `FAILED`

这会导致两个问题：

- “未初始化”被错误地表现成“运行失败”
- 用户完成初始化配置后，运行时里已经留下失败任务和失败状态，页面体验很差

## 2. 目标

目标不是阻止后端启动，而是阻止“未初始化时进入调度”。

期望行为：

- 未配置 LLM 时，后端、Web Console、配置接口、状态接口都可以正常工作
- 未配置 LLM 时，不允许房间进入 Agent 调度链路
- 用户完成 Quick Init 后，可以统一开启调度
- 不需要在每个 Agent 上单独做“失败恢复”作为主流程

## 3. 核心思路

为 `schedulerService` 增加一个“全局调度闸门”与全局状态。

这个状态不是“进程是否在运行”，而是“当前是否允许投递和消费调度任务”。

建议由 `schedulerService` 维护三态：

```python
from enum import Enum, auto


class SchedulerState(Enum):
    STOPPED = auto()
    BLOCKED = auto()
    RUNNING = auto()
```

语义如下：

- `STOPPED`
  - scheduler 尚未启动，或已 shutdown
- `BLOCKED`
  - 后端和 scheduler 已运行，但当前不允许进入调度
  - 典型原因：尚未配置可用的 LLM
- `RUNNING`
  - 允许房间激活、允许创建 Agent task、允许 consumer 消费

说明：

- 这里不建议叫 `pending`
- 因为当前语义不是“等待某个异步操作自动完成”，而是“被前置条件阻塞”
- `blocked` / `waiting_for_init` 比 `pending` 更准确

## 4. 建议状态流转

### 4.1 启动时

- `schedulerService.startup()` 初始化状态
- 若 `configUtil.is_initialized()` 为 `False`，状态设为 `BLOCKED`
- 若已存在可用 LLM，状态设为 `RUNNING`

### 4.2 Quick Init 成功后

- `QuickInitHandler` 保存配置成功
- 调用 `schedulerService.enable_dispatch()`
- 全局状态从 `BLOCKED` 切换到 `RUNNING`
- 再统一触发 `start_scheduling()`

### 4.3 用户后来又禁用了全部 LLM

- 设置页把最后一个可用服务禁用后
- 调用 `schedulerService.disable_dispatch(reason="llm_uninitialized")`
- 全局状态从 `RUNNING` 切换到 `BLOCKED`

### 4.4 进程退出时

- `schedulerService.shutdown()` 把状态切到 `STOPPED`

## 5. 调度闸门应拦截的位置

为了避免“绕过某个入口仍然进入调度”，闸门至少要拦住两处。

### 5.1 拦截房间激活

位置：

- `teamService.restore_team()`
- `schedulerService.start_scheduling()`

建议：

- `restore_team()` 在未初始化时仍恢复 Agent / Room / history
- 但不触发真正的 `start_scheduling()`
- `start_scheduling()` 内部也要再做一次状态检查，作为兜底防线

这样即使未来有别的调用点误调 `start_scheduling()`，也不会真的激活房间调度。

### 5.2 拦截任务创建

位置：

- `schedulerService._on_room_status_changed()`

建议：

- 只有当状态是 `RUNNING` 时，才允许创建 `GtAgentTask`
- 只有当状态是 `RUNNING` 时，才允许启动 `agent.start_consumer_task()`

这是第二道保险。

即使某个 Room 因历史状态恢复或别的逻辑发布了事件，只要 scheduler 仍是 `BLOCKED`，也不会向下走到 Agent 推理。

## 6. 为什么不建议以“逐个 Agent 恢复”为主方案

如果把主逻辑做成：

- 先允许调度报错
- 然后用户初始化成功后，再逐个恢复 FAILED Agent / FAILED task

会有几个问题：

- “未初始化”与“真实运行失败”被混在一起
- 需要识别并清理特定错误文案
- 恢复逻辑会散落到 Agent / Task / 前端多个层面
- 首次进入页面时，用户已经看到了失败态，体验仍然不好

因此更合理的主路径应该是：

- 未初始化时，根本不要进入调度
- 初始化完成后，再统一放开调度

## 7. 对现有运行时恢复流程的影响

建议保留“基础恢复”，只禁止“进入调度”。

也就是说，未初始化时仍然可以做：

- 加载 Team runtime
- 加载 Agent 实例
- 加载房间
- 恢复历史消息
- 恢复房间 read index / turn pos

但不要做：

- 激活房间调度
- 创建 Agent task
- 启动 Agent consumer
- 发起 LLM 推理

这样可以保证：

- 页面仍能展示已有 Team / Room / 历史消息
- Quick Init 完成后，无需重新加载整套基础数据结构
- 只需要统一开启调度即可

## 8. Quick Init 完成后的建议行为

`QuickInitHandler` 不应只负责写 `setting.json`，还应负责把系统从“未初始化阻塞态”切到“可调度态”。

建议在保存成功后追加：

1. 更新内存配置
2. 调用 `schedulerService.enable_dispatch()`
3. 统一触发 `schedulerService.start_scheduling()`

这样用户在 Quick Init 完成后，不需要手动重启后端，也不需要逐个恢复 Agent。

## 9. 历史脏数据处理

如果系统里已经存在“旧版本逻辑”留下的失败任务，仍需要一个兜底处理。

这部分不建议作为主流程，但建议作为兼容修复：

- 在 `enable_dispatch()` 时，识别错误原因为“未配置可用的 LLM 服务”的失败任务
- 统一恢复或清理这类失败任务

注意：

- 这只用于兼容历史脏数据
- 不应成为未来正常流程的依赖
- 正常情况下，闸门生效后不应再产生这类失败任务

## 10. 前端配合建议

后端修复是主方案，前端只做体验收尾。

建议前端做两件事：

- 首屏优先读取 `/system/status.json`
- Quick Init 成功后，重新 bootstrap 页面状态，并清空旧错误提示

这样可以避免：

- 首屏并行请求留下的错误 toast 继续残留
- 配置完成后页面仍显示旧错误文案

## 11. 建议的最小落地范围

如果按“最小可用改动”推进，建议先做以下几项：

1. `schedulerService` 增加全局状态：`STOPPED / BLOCKED / RUNNING`
2. `startup()` 按 `configUtil.is_initialized()` 初始化状态
3. `start_scheduling()` 在 `BLOCKED` 时直接返回
4. `_on_room_status_changed()` 在 `BLOCKED` 时不创建 task
5. `QuickInitHandler` 成功后统一 `enable_dispatch()` 并调用 `start_scheduling()`
6. 前端 Quick Init 成功后清理旧错误并重新加载系统状态

## 12. 待讨论问题

以下问题建议在实现前确认：

- `schedulerService` 的状态是否只在内存中维护，还是要通过 `/system/status.json` 暴露给前端
- `BLOCKED` 的原因是否需要细分，例如 `llm_uninitialized` / `manual_pause`
- 历史失败任务在启用调度时应“恢复”还是“删除”
- 用户在设置页禁用最后一个 LLM 时，是否需要立即暂停所有活跃 consumer

## 13. 当前建议结论

本问题的主修复方向应是：

- 引入 `schedulerService` 的全局调度状态
- 用 `BLOCKED` 表达“后端在运行，但当前禁止进入调度”
- 未初始化时只恢复基础 runtime，不进入调度
- 初始化完成后统一开启调度

这个方案比“让每个 Agent 在失败后自行恢复”更简单，也更符合系统语义。
