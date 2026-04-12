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

## 0. 当前基线

本文档基于当前代码状态讨论，先说明一个已经完成的前置变化：

- `schedulerService` 已不再负责“挂住主进程直到退出”
- `schedulerService` 当前只保留普通 service 职责：`startup()` / `start_schedule_team()` / `shutdown()`
- 主进程退出等待现在由 `backend_main` 自己维护，通过内部 `_shutdown_event` + `request_shutdown()` 实现

也就是说，本文讨论的“调度状态”仅指 `schedulerService` 内部的调度状态，不涉及主进程生命周期管理。

## 1. 问题定义

当前实现中，“未配置 LLM”虽然不会阻止后端启动，但不会阻止调度启动。

现有链路大致如下：

1. 后端启动
2. 恢复 Team runtime
3. `teamService.restore_team()` 调用 `schedulerService.start_schedule_team()`
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

为 `schedulerService` 增加一个“全局调度闸门”与调度状态。

这里需要区分两层状态：

- service 生命周期状态
  - 所有 service 统一只有两态：`startup / shutdown`
- scheduler 的调度状态
  - 仅属于 `schedulerService` 自身
  - 表示“当前是否允许进入调度链路”

建议由 `schedulerService` 维护一个 `ScheduleState`：

```python
from enum import Enum, auto


class ScheduleState(Enum):
    STOPPED = auto()
    BLOCKED = auto()
    RUNNING = auto()
```

语义如下：

- `STOPPED`
  - 调度未开启，或被显式停止
- `BLOCKED`
  - 尝试开启调度，但前置条件不满足
  - 典型原因：尚未配置可用的 LLM
- `RUNNING`
  - 允许房间激活、允许创建 Agent task、允许 consumer 消费

说明：

- 这里不建议叫 `pending`
- 因为当前语义不是“等待某个异步操作自动完成”，而是“被前置条件阻塞”
- `blocked` / `waiting_for_init` 比 `pending` 更准确

基于这个设计，`schedulerService` 建议补充或调整以下接口：

| 方法 | 类型 | 说明 |
|------|------|------|
| `startup()` | 保留 | 启动 service，并自动调用 `start_schedule()` |
| `shutdown()` | 保留 | 关闭 service |
| `get_schedule_state() -> ScheduleState` | 新增 | 返回当前调度状态 |
| `is_schedule_enabled() -> bool` | 新增 | 返回当前是否允许进入调度链路 |
| `start_schedule() -> None` | 新增 | 执行调度前检查；成功进入 `RUNNING` 并自动调用 `start_schedule_team()`，失败进入 `BLOCKED` |
| `stop_schedule() -> None` | 新增 | 显式停止调度，切换到 `STOPPED` |
| `start_schedule_team(team_name: str \| None = None)` | 保留/调整 | 实际激活房间调度；仅在 `RUNNING` 时执行 |

## 4. 建议状态流转

### 4.1 启动时

- `schedulerService.startup()` 启动 service
- `startup()` 内部自动调用 `start_schedule()`
- 若已存在可用 LLM，调度状态切到 `RUNNING`，并自动触发 `start_schedule_team()`
- 若尚未初始化，调度状态切到 `BLOCKED`

### 4.2 Quick Init 成功后

- `QuickInitHandler` 保存配置成功
- 调用 `schedulerService.start_schedule()`
- 若存在可用 LLM，调度状态切到 `RUNNING`
- 若条件仍不满足，调度状态维持 `BLOCKED`

### 4.3 用户后来又禁用了全部 LLM

- 设置页把最后一个可用服务禁用后
- 调用 `schedulerService.stop_schedule()`
- 调度状态切到 `STOPPED`

### 4.4 进程退出时

- `schedulerService.shutdown()` 关闭 service
- `backend_main` 仍负责等待退出信号并统一调用各 service 的 shutdown

## 5. 调度闸门应拦截的位置

为了避免“绕过某个入口仍然进入调度”，闸门至少要拦住两处。

### 5.1 拦截房间激活

位置：

- `teamService.restore_team()`
- `schedulerService.start_schedule_team()`

建议：

- `restore_team()` 在未初始化时仍恢复 Agent / Room / history
- 但不触发真正的 `start_schedule_team()`
- `start_schedule_team()` 内部也要再做一次调度状态检查，作为兜底防线

这样即使未来有别的调用点误调 `start_schedule_team()`，也不会真的激活房间调度。

注意：

- 这里不需要恢复 `schedulerService.run()` 一类阻塞入口
- 调度闸门应直接建立在当前的 `startup()` / `start_schedule()` / `start_schedule_team()` / `shutdown()` 结构上

### 5.2 拦截任务创建

位置：

- `schedulerService._on_room_status_changed()`

建议：

- 调度状态非 `RUNNING` 时，既不创建 `GtAgentTask`，也不启动 `agent.start_consumer_task()`
- 仅在 `RUNNING` 状态下才进入完整的任务创建与消费链路

理由：

- 当前实现中，task 创建和 consumer 启动是连续动作；在 `BLOCKED` 时创建 task 却不消费，后续切到 `RUNNING` 时无人消费这些积压 task
- `start_schedule()` 成功后会自动调用 `activate_rooms()`，重新触发事件链，不需要提前建 task

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
2. 调用 `schedulerService.start_schedule()`

这样用户在 Quick Init 完成后，不需要手动重启后端，也不需要逐个恢复 Agent。

关于内存配置一致性：

- `configUtil.update_setting(mutator)` 会同时更新磁盘文件和内存中的 `SettingConfig`
- `llmService.infer()` 在运行时通过 `configUtil.get_app_config().setting.current_llm_service` 动态读取配置
- `current_llm_service` 是一个计算属性，每次调用时从 `llm_services` 列表中筛选
- 因此 `update_setting` 完成后，内存中的 LLM 配置立即可用，不需要额外的 `llmService.reload()`

## 9. 前端配合建议

后端修复是主方案，前端只做体验收尾。

建议前端做两件事：

- 首屏优先读取 `/system/status.json`
- Quick Init 成功后，重新 bootstrap 页面状态，并清空旧错误提示

这样可以避免：

- 首屏并行请求留下的错误 toast 继续残留
- 配置完成后页面仍显示旧错误文案

## 10. 建议的最小落地范围

如果按“最小可用改动”推进，建议先做以下几项：

1. `schedulerService` 增加 `ScheduleState`：`STOPPED / BLOCKED / RUNNING`
2. `startup()` 内部自动调用 `start_schedule()`
3. `start_schedule()` 负责根据当前条件切换到 `RUNNING` 或 `BLOCKED`，并在成功时自动调用 `start_scheduling(team_name=None)` 激活所有已恢复的 team
4. `start_schedule_team()` 在调度状态非 `RUNNING` 时直接返回，否则激活房间
5. `_on_room_status_changed()` 在调度状态非 `RUNNING` 时跳过整个处理（不创建 task，也不启动 consumer）
6. `QuickInitHandler` 成功后调用 `start_schedule()`
7. 前端 Quick Init 成功后清理旧错误并重新加载系统状态

补充说明：

- `backend_main` 的退出等待模型已完成，不属于本方案改动范围
- 本方案只处理“允许不允许调度”，不再讨论谁来 `await` scheduler

## 11. 已确认的设计决策

以下问题已在讨论中确认：

- **`ScheduleState` 暴露给前端**：在 `/system/status.json` 返回 `schedule_state` 字段（值为 `stopped` / `blocked` / `running`），前端可据此显示"调度已阻塞"vs"调度运行中"
- **`BLOCKED` 原因不细分**：当前只有 `llm_uninitialized` 一种阻塞原因，暂不引入细分枚举
- **禁用最后一个 LLM 时立即暂停**：调用 `stop_schedule()` 切到 `STOPPED`，已在运行中的 consumer 让其自然失败（`infer()` 会因无可用 LLM 报错），不需要强制 kill

## 12. 当前建议结论

本问题的主修复方向应是：

- 保持 service 生命周期与调度状态分离
- 为 `schedulerService` 引入 `ScheduleState`
- 未初始化时只恢复基础 runtime，不进入调度
- 初始化完成后统一尝试开启调度，再触发调度

这个方案比“让每个 Agent 在失败后自行恢复”更简单，也更符合系统语义。
