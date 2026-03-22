# V9: 数据持久化与重启恢复 - 开发任务表

## 任务概览

V9 的目标是在现有 V8 运行时架构上补齐最小可用的持久化与重启恢复能力。当前方案采用 SQLite 作为本地状态库，引入 `ormService + DAL Manager + persistenceService` 三层结构，并将恢复编排统一收敛到 `persistenceService`。

本版本的核心范围包括：

- 持久化房间消息 `room_messages`
- 持久化房间读取进度 `rooms.agent_read_index`
- 持久化 Agent 私有历史 `agent_histories`
- 将房间“创建”和“开始调度”解耦
- 启动时由 `main -> persistenceService` 统一完成恢复
- 恢复后默认不自动恢复聊天室调度，需由显式入口手动触发

共拆分为 8 个任务，按依赖关系排序。

---

## 任务列表

### 任务 1: 新增 ORM 模型与数据库基础设施

**描述**: 建立 V9 的数据库基础设施，包括 `ormService` 和三张核心状态表对应的 ORM 模型。

**依赖**: 无

**文件**:
- `src/service/ormService.py`（新建）
- `src/model/db_model/base.py`（新建或补充）
- `src/model/db_model/GtRoomMessage.py`（新建）
- `src/model/db_model/GtRoom.py`（新建）
- `src/model/db_model/GtAgentHistory.py`（新建）

**子任务**:
- [ ] 新增 `ormService.py`，负责数据库连接初始化与关闭
- [ ] 在 `ormService` 中支持从配置读取 `db_path`
- [ ] 建立 `room_messages` ORM 模型
- [ ] 建立 `rooms` ORM 模型
- [ ] 建立 `agent_histories` ORM 模型
- [ ] 启动时自动检查并创建缺失表
- [ ] 为 `room_messages(room_key, id)` 创建索引
- [ ] 为 `agent_histories(agent_key, seq)` 创建唯一索引

**验收标准**:
- `ormService.startup()` 能成功初始化 SQLite 数据库
- 数据库中能自动创建 `room_messages`、`rooms`、`agent_histories` 三张表
- 重复启动不会重复建表或报错
- `ormService.shutdown()` 能正常关闭连接

---

### 任务 2: 实现 DAL Manager 层

**描述**: 为三类持久化对象建立独立的 DAL Manager，封装查询和幂等写入逻辑。

**依赖**: 任务 1

**文件**:
- `src/dal/db/gtRoomMessageManager.py`（新建）
- `src/dal/db/gtRoomManager.py`（新建）
- `src/dal/db/gtAgentHistoryManager.py`（新建）

**子任务**:
- [ ] 在 `gtRoomMessageManager` 中实现 `append_room_message(...)`
- [ ] 在 `gtRoomMessageManager` 中实现 `get_room_messages(room_key, after_id=None)`
- [ ] 在 `gtRoomManager` 中实现 `upsert_room(room_key, agent_read_index)`
- [ ] 在 `gtRoomManager` 中实现 `get_room(room_key)`
- [ ] 在 `gtAgentHistoryManager` 中实现 `append_agent_history_messages(agent_key, messages)`
- [ ] 在 `gtAgentHistoryManager` 中实现 `get_agent_history(agent_key)`
- [ ] 为共享筛选逻辑补充 `_build_xxx_condition()` 内部方法

**验收标准**:
- 三个 Manager 都能在不依赖 service 层的情况下完成独立读写
- `gtRoomManager.upsert_room()` 能覆盖更新同一个 `room_key`
- `gtAgentHistoryManager.get_agent_history()` 返回结果按 `seq` 顺序 stable
- DAL 层不向上暴露 SQL 细节

---

### 任务 3: 新增 persistenceService 并实现恢复编排

**描述**: 新增 `persistenceService`，统一承接状态持久化与恢复编排逻辑。

**依赖**: 任务 1、任务 2

**文件**:
- `src/service/persistenceService.py`（新建）

**子任务**:
- [ ] 实现 `startup(enabled: bool)` 和 `shutdown()`
- [ ] 实现 `is_enabled()`
- [ ] 实现 `append_room_message(...)`
- [ ] 实现 `save_room(room_key, agent_read_index)`
- [ ] 实现 `append_agent_history_messages(agent_key, messages)`
- [ ] 实现 `load_room_messages(room_key)`
- [ ] 实现 `load_room(room_key)`
- [ ] 实现 `load_agent_history(agent_key)`
- [ ] 实现 `restore_runtime_state(agents, rooms)`，统一编排恢复
- [ ] 在恢复过程中按正确顺序注入 Agent 历史、Room 消息、Room 读取进度，并驱动 Room 重建状态

**验收标准**:
- `persistenceService` 不直接写 SQL，只通过 Manager 层访问数据库
- `restore_runtime_state()` 能遍历所有已创建的 Agent / Room 对象并尝试恢复
- 对不存在历史记录的 Agent / Room，恢复流程应平稳跳过
- 恢复逻辑集中在 `persistenceService`，不散落到 `main` / `roomService` / `agentService`

---

### 任务 4: 改造 roomService，解耦建房与调度启动

**描述**: 调整 `roomService`，使房间创建时不自动发送首个调度事件，并提供状态注入接口。

**依赖**: 无

**文件**:
- `src/service/roomService.py`（修改）

**子任务**:
- [ ] 修改 `create_room`，创建房间时不自动发布 `ROOM_AGENT_TURN`
- [ ] 保留首次创建房间时写入系统建房消息的能力
- [ ] 新增 `inject_history_messages(...)`
- [ ] 新增 `inject_agent_read_index(...)`
- [ ] 新增 `rebuild_state_from_history(...)`
- [ ] 新增 `start_scheduling()`
- [ ] 确保 `rebuild_state_from_history(...)` 在恢复时不会重复落盘或重复广播历史消息
- [ ] 补充读取进度导出能力，便于持久化 `_agent_read_index`

**验收标准**:
- 新创建的房间对象在未显式调用 `start_scheduling()` 前不会自动开始调度
- `inject_history_messages(...)` 后，房间消息列表与数据库内容一致
- `inject_agent_read_index(...)` 能正确恢复房间的已读进度
- `rebuild_state_from_history(...)` 能根据历史消息重建 `_state / _turn_index / _turn_pos / _round_skipped`

---

### 任务 5: 改造 agentService，支持历史逐条导出与注入

**描述**: 为 `Agent` 增加历史消息的逐条持久化辅助能力。

**依赖**: 无

**文件**:
- `src/service/agentService.py`（修改）

**子任务**:
- [ ] 新增 `dump_history_messages()`
- [ ] 新增 `inject_history_messages(items)`
- [ ] 为 `_history` 增量写入提供必要的序号 or 偏移辅助
- [ ] 在 `sync_room()` 后识别新增的 `_history` 消息
- [ ] 在普通 LLM 模式下，一轮 `chat()` 完成后把新增消息交给 `persistenceService`
- [ ] 在 SDK 模式下，一轮执行结束后把新增消息交给 `persistenceService`
- [ ] 确保恢复后重新初始化 SDK 会话，而不是尝试恢复底层 SDK 连接

**验收标准**:
- `dump_history_messages()` 输出结果可直接写入 `agent_histories.message_json`
- `inject_history_messages(items)` 能按顺序恢复 `_history`
- Agent 恢复后能继续基于旧上下文工作，不出现明显“失忆”

---

### 任务 6: 改造 main.py 启动流程与 schedulerService 显式调度入口

**描述**: 调整系统启动顺序，先创建对象，再恢复状态；保留显式调度入口，但恢复完成后默认不自动开启调度。

**依赖**: 任务 3、任务 4、任务 5

**文件**:
- `src/main.py`（修改）
- `src/service/schedulerService.py`（修改）

**子任务**:
- [ ] 在 `main.py` 中接入持久化配置读取
- [ ] 在启动顺序中加入 `ormService.startup()`
- [ ] 在启动顺序中加入 `persistenceService.startup()`
- [ ] 先创建 Team / Agent / Room 对象骨架
- [ ] 再调用 `persistenceService.restore_runtime_state(agents, rooms)`
- [ ] 恢复完成后再启动 `scheduler`
- [ ] 改造 `schedulerService.replay_scheduling_rooms()`
- [ ] `replay_scheduling_rooms()` 改为调用 `room.start_scheduling()`，而不是依赖建房时的隐式首发事件
- [ ] 确保恢复完成后默认不自动调用 `replay_scheduling_rooms()`
- [ ] 在关闭流程中补上 `persistenceService.shutdown()` 和 `ormService.shutdown()`

**验收标准**:
- 启动时不会在状态恢复前提前触发调度
- 恢复完成后，处于 `SCHEDULING` 的房间会保留正确调度位置，但不会自动继续执行
- 已停止的房间不会被错误重新激活
- 启停流程完整，不遗留未关闭资源

---

### 任务 7: 补充配置与兼容逻辑

**描述**: 增加 V9 持久化配置项，并保证关闭持久化时系统仍按旧模式运行。

**依赖**: 任务 1、任务 3、任务 6

**文件**:
- `src/util/config_util.py`（修改）
- 与配置读取相关的启动代码（修改）

**子任务**:
- [ ] 在配置结构中新增 `persistence.enabled`
- [ ] 在配置结构中新增 `persistence.db_path`
- [ ] 为缺失 `persistence` 配置时提供默认值
- [ ] `enabled=false` 时跳过数据库初始化和状态恢复
- [ ] `enabled=false` 时维持 V8 纯内存模式行为

**验收标准**:
- 配置文件中可显式开启/关闭持久化
- 未配置持久化时系统可正常启动
- 关闭持久化时，系统行为与 V8 保持兼容

---

### 任务 8: 单元测试与重启恢复集成测试

**描述**: 为 V9 的持久化、恢复 and 显式续跑能力补齐测试。

**依赖**: 任务 1、2、3、4、5、6、7

**文件**:
- `tests/unit/` 下相关测试文件（新增/修改）
- `tests/integration/` 下相关测试文件（新增/修改）

**子任务**:
- [ ] 为 `ormService` 补充建表与关闭测试
- [ ] 为各 DAL Manager 补充插入/查询测试
- [ ] 为 `persistenceService.restore_runtime_state()` 补充恢复编排测试
- [ ] 为 `roomService.rebuild_state_from_history()` 补充状态重建测试
- [ ] 为 `inject_agent_read_index(...)` 补充读取进度恢复测试
- [ ] 为 `agentService.inject_history_messages(...)` 补充历史恢复测试
- [ ] 编写“正常对话 -> 关闭服务 -> 再次启动 -> 状态恢复但不自动继续对话”的集成测试
- [ ] 编写“恢复完成后手动调用调度入口 -> 继续对话”的集成测试
- [ ] 编写“房间处于 scheduling 中途异常退出 -> 重启恢复”的集成测试
- [ ] 编写“enabled=false” 兼容模式测试

**验收标准**:
- V9 核心恢复链路有自动化测试覆盖
- 重启后消息、读取进度和 Agent 历史均能恢复
- 调度位置恢复正确，不重复消费旧消息，也不漏消费未读消息
- 恢复完成后默认不会自动恢复聊天室调度

---

## 任务依赖关系图

```text
任务 1 (ORM 基础设施)
    └─ 任务 2 (DAL Manager)
            └─ 任务 3 (persistenceService)

任务 4 (roomService 改造) ──┐
任务 5 (agentService 改造) ─┼─ 任务 6 (main + scheduler 恢复流程)
任务 3 (persistenceService) ─┘

任务 1 ─┐
任务 3 ─┼─ 任务 7 (配置与兼容)
任务 6 ─┘

任务 2 ─┐
任务 4 ─┼─ 任务 8 (测试)
任务 5 ─┤
任务 6 ─┤
任务 7 ─┘
```

---

## 开发顺序建议

**推荐顺序**: 任务 1 → 任务 2 → 任务 3 → 任务 4 + 任务 5（并行）→ 任务 6 → 任务 7 → 任务 8

**并行开发机会**:
- 任务 4 和任务 5 可以并行，它们分别聚焦 Room 和 Agent
- 任务 7 可以在任务 6 接近完成时并行推进
- 测试任务应在核心实现稳定后集中补齐

---

## 文件变更清单

### 新增文件

- `src/service/ormService.py`
- `src/service/persistenceService.py`
- `src/model/db_model/GtRoomMessage.py`
- `src/model/db_model/GtRoom.py`
- `src/model/db_model/GtAgentHistory.py`
- `src/dal/db/gtRoomMessageManager.py`
- `src/dal/db/gtRoomManager.py`
- `src/dal/db/gtAgentHistoryManager.py`
- `docs/versions/v9/v9_step3_tasks.md`

### 重点修改文件

- `src/main.py`
- `src/service/roomService.py`
- `src/service/agentService.py`
- `src/service/schedulerService.py`
- `src/util/config_util.py`

---

## 测试检查清单

- [ ] SQLite 数据库能自动初始化并创建三张状态表
- [ ] `room_messages` 能持续写入并按房间查询
- [ ] `rooms.agent_read_index` 能正确保存和恢复
- [ ] `agent_histories` 能按 `seq` 追加并有序恢复
- [ ] `create_room` 不会在对象创建时自动启动调度
- [ ] `start_scheduling()` 能在恢复完成后显式启动调度
- [ ] `restore_runtime_state()` 能统一恢复 Agent / Room 状态
- [ ] 重启后房间能恢复到正确调度位置
- [ ] 恢复完成后默认不会自动恢复聊天室调度
- [ ] 重启后 Agent 不会重复消费旧消息
- [ ] `enabled=false` 时系统仍按纯内存模式运行

---

## 验收标准（最终）

- [ ] 服务重启后，历史消息仍可查询和展示
- [ ] 服务重启后，房间能够恢复到正确调度位置
- [ ] 服务重启后默认不自动恢复聊天室调度，需显式触发继续运行
- [ ] Agent 私有历史能够恢复，跨房间上下文能力保留
- [ ] `_agent_read_index` 能正确恢复，不重复消费旧消息
- [ ] 建房与调度启动彻底解耦，恢复阶段不会提前触发执行
- [ ] 持久化可通过配置启用或关闭
