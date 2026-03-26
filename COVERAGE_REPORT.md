# TeamAgent 功能覆盖报告 (2026-03-26)

## 1. 总体概览

| 维度 | 统计值 | 备注 |
|---|---|---|
| **总行数 (Stmts)** | 3614 | |
| **整体通过率** | 100% | 168 + 11(new) = 179 tests |
| **重点模块覆盖率** | 见下表 | 已大幅提升关键 Service 与 Driver 的覆盖 |

---

## 2. 核心功能覆盖详情

### 2.1 业务服务层 (Service Layer)
负责核心业务逻辑。

| 功能模块 | 关键文件 | 覆盖率 | 状态 | 备注 |
|---|---|---|---|---|
| Agent 核心逻辑 | `agentService/core.py` | 89% | ✅ 优秀 | |
| 房间管理 | `roomService.py` | 84% | ✅ 良好 | |
| 任务调度 | `schedulerService.py` | **86%** | ✅ 优秀 | 从 69% 提升，补全了配置刷新与团队停止逻辑 |
| 函数工具系统 | `funcToolService/` | 91-98% | ✅ 优秀 | |
| 消息总线 | `messageBus.py` | 90% | ✅ 优秀 | |

### 2.2 Agent 驱动程序 (Drivers)
不同 LLM 后端的适配逻辑。

| 功能模块 | 关键文件 | 覆盖率 | 状态 | 备注 |
|---|---|---|---|---|
| Native 驱动 | `nativeDriver.py` | **94%** | ✅ 优秀 | 从 34% 提升，新增了重试逻辑与最大调用次数测试 |
| TSP 驱动 | `tspDriver.py` | **78%** | ✅ 良好 | 从 66% 提升，新增了 Mock 单元测试覆盖错误处理路径 |
| Claude SDK 驱动 | `claudeSdkDriver.py` | 51% | ⚠️ 中等 | |

### 2.3 数据访问层 (DAL)
负责数据库直接交互。

| 功能模块 | 关键文件 | 覆盖率 | 状态 | 备注 |
|---|---|---|---|---|
| 领域管理 (Manager) | `dal/db/*.py` | 87-99% | ✅ 优秀 | |

---

## 3. 新增测试点说明 (2026-03-26 增补)

### 3.1 SchedulerService
- **配置热加载**: 验证 `refresh_team_config` 是否能正确更新调度参数。
- **团队生命周期**: 验证 `stop_team` 是否能物理中断该团队下所有 Agent 的后台任务。
- **鲁棒性**: 增加 Agent 找不到、OPERATOR 虚拟身份过滤等边缘路径测试。

### 3.2 NativeDriver (新增 `tests/unit/test_native_driver.py`)
- **强制工具提示**: 验证当 Agent 返回纯文本而非工具调用时，驱动是否能正确注入 Hint 并重试。
- **截断保护**: 验证达到 `max_function_calls` 后是否能正常终止防止无限循环。
- **终止符识别**: 验证 `finish_chat_turn` 的语义识别。

### 3.3 TspDriver (增强 `tests/unit/test_tsp_driver.py`)
- **协议异常**: 验证 TSP Client 断开连接后的 `_fail_pending` 自动异常分发。
- **多级工具分发**: 验证本地工具（Local）与 TSP 远程工具的冲突处理与分发逻辑。
- **鲁棒性**: 补全了 JSON 解析失败、TSP 后端报错（TeamAgentException）等错误路径。
