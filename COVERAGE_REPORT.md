# TeamAgent 功能覆盖报告 (2026-03-26)

## 1. 总体概览

| 维度 | 统计值 | 备注 |
|---|---|---|
| **总行数 (Stmts)** | 3614 | |
| **整体通过率** | 100% | 168 + 13(new) = 181 tests |
| **重点模块覆盖率** | 见下表 | 已大幅提升关键 Service、Driver 以及 API 的覆盖 |

---

## 2. 核心功能覆盖详情

### 2.1 业务服务层 (Service Layer)
负责核心业务逻辑。

| 功能模块 | 关键文件 | 覆盖率 | 状态 | 备注 |
|---|---|---|---|---|
| Agent 核心逻辑 | `agentService/core.py` | 89% | ✅ 优秀 | |
| 房间管理 | `roomService.py` | 84% | ✅ 良好 | |
| 任务调度 | `schedulerService.py` | **86%** | ✅ 优秀 | 涵盖配置刷新与团队生命周期管理 |
| 函数工具系统 | `funcToolService/` | 91-98% | ✅ 优秀 | |
| 消息总线 | `messageBus.py` | 90% | ✅ 优秀 | |

### 2.2 Agent 驱动程序 (Drivers)
不同 LLM 后端的适配逻辑。

| 功能模块 | 关键文件 | 覆盖率 | 状态 | 备注 |
|---|---|---|---|---|
| Native 驱动 | `nativeDriver.py` | **94%** | ✅ 优秀 | 涵盖重试逻辑、Hint 注入与最大调用次数 |
| TSP 驱动 | `tspDriver.py` | **78%** | ✅ 良好 | 涵盖远程工具分发与协议异常处理 |
| Claude SDK 驱动 | `claudeSdkDriver.py` | 51% | ⚠️ 中等 | |

### 2.3 接口层 (API Layer)
API 层采用独立进程运行，整体覆盖率显著提升，但部分由客户端触发的内部路径统计仍有待合并。

| 功能模块 | 关键 Handler | 状态 | 备注 |
|---|---|---|---|
| 聊天/消息 API | `RoomMessagesHandler` | ✅ 覆盖 | 涵盖发送、拉取、私有房间自动触发 |
| 实时推送 API | `EventsWsHandler` | ✅ 覆盖 | 涵盖 WebSocket 消息事件推送 |
| 团队管理 API | `TeamList/Create/Detail` | ✅ 覆盖 | |
| **团队维护 API** | `TeamModify/Delete` | ✅ **新增** | 涵盖修改与删除逻辑 |
| **房间维护 API** | `TeamRoom lifecycle` | ✅ **新增** | 涵盖房间 CRUD 及成员管理 |

### 2.4 数据访问层 (DAL)
负责数据库直接交互。

| 功能模块 | 关键文件 | 覆盖率 | 状态 | 备注 |
|---|---|---|---|---|
| 领域管理 (Manager) | `dal/db/*.py` | 87-99% | ✅ 优秀 | |

---

## 3. 新增测试点说明 (2026-03-26 增补)

### 3.1 接口层 (API) - 新增 `tests/api/test_config_api.py`
- **团队维护**: 验证 `POST /teams/{id}/modify.json` 和 `POST /teams/{id}/delete.json`。
- **房间生命周期**: 验证 Team 下 Room 的创建、详情查询、参数修改（type/topic/max_turns）和物理删除。
- **成员管理**: 验证 `POST /teams/{id}/rooms/{rid}/members/modify.json` 是否能正确同步成员列表。
- **ID 稳定性**: 修复了修改房间配置导致 ID 变动的 Bug，现在修改配置不再导致房间被删除重建。

### 3.2 SchedulerService
- **配置热加载**: 验证 `refresh_team_config` 是否能正确更新调度参数。
- **团队生命周期**: 验证 `stop_team` 是否能物理中断该团队下所有 Agent 的后台任务。

### 3.3 NativeDriver & TspDriver
- **Native 驱动重试**: 验证当 Agent 返回纯文本而非工具调用时，驱动是否能注入 Hint 并重试。
- **TSP 协议鲁棒性**: 验证连接断开后的异常分发与 JSON 解析失败处理。
