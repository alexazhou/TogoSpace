# V14: Agent 团队感知与协作能力 - 技术文档

## 1. 架构概览

V14 的核心变更是在现有 `funcToolService` 工具注册体系中新增 4 个团队感知工具函数。这些工具不引入新的基础设施，而是组合调用已有的 service 层能力：

```text
LLM 推理返回 tool_call
        │
        ▼
  toolRegistry.execute_tool_call()
        │
        ▼
  funcToolService.run_tool_call(function_args, context)
        │
        ├── _context 注入（team_id, agent_name, chat_room）
        │
        ▼
  tools.py 中的工具函数
        │
        ├── get_dept_info()   ──> deptService.get_dept_tree() / gtDeptManager
        ├── get_room_info()   ──> roomService (内存 ChatRoom)
        ├── get_agent_info()  ──> agentService.get.togo_agents() + deptService
        └── wake_up_agent()   ──> agentService.get_agent() + agent.resume_failed()
```

设计要点：

- **纯工具层扩展**：所有新增代码集中在 `tools.py`，不修改 service 层接口，不新增 Controller / 路由。
- **复用 `_context` 注入**：与 `send_chat_msg`、`finish_chat_turn` 等已有工具一致，通过 `ToolCallContext` 获取 `team_id` 和 `agent_name`。
- **复用已有 resume 逻辑**：`wake_up_agent` 内部调用 `agent.resume_failed()`，与 `AgentResumeHandler`（`POST /agents/<id>/resume.json`）使用同一条代码路径。
- **活动记录自动覆盖**：工具调用产生的 `tool_call` 活动记录由 `AgentTurnRunner._execute_tool_call()` 统一处理，V14 工具无需额外埋点。
- **临时规避循环导入**：由于当前存在 `agentService -> funcToolService` 的依赖链，`tools.py` 中对 `agentService`、`deptService` 等模块的访问采用**函数内动态 import**，不在模块顶层直接引入；后续再通过独立 facade/query service 做彻底治理。

---

## 2. 工具函数实现

### 2.1 数据源映射

每个工具的数据来源及所依赖的 service / DAL：

| 工具 | 数据源 | 关键调用 |
|------|--------|---------|
| `get_dept_info` | 部门树（DB） | `deptService.get_dept_tree(team_id)` |
| `get_room_info` | 内存 ChatRoom | `roomService.get_team_rooms(team_id)` / `roomService.get_room(room_id)` |
| `get_agent_info` | 内存 Agent + DB 部门 | `agentService.get.togo_agents(team_id)` + `deptService.get_agent_dept()` |
| `wake_up_agent` | 内存 Agent | `agentService.get_agent(id)` → `agent.resume_failed()` |

### 2.2 get_dept_info

```python
async def get_dept_info(dept_id: Optional[int] = None, _context: ToolCallContext = None) -> dict:
    """查询部门信息。不传 dept_id 时返回根部门（整个团队），传入 dept_id 时返回指定部门及其子树。

    Args:
        dept_id: 部门 ID，省略时返回根部门
    """
```

实现要点：

1. 通过 `_context.team_id` 获取 Team ID
2. 调用 `deptService.get_dept_tree(team_id)` 获取完整部门树
3. 若 `dept_id` 为 None，返回根节点及完整子树
4. 若 `dept_id` 非 None，递归查找目标节点；找不到时返回 `success: false`
5. 部门节点序列化包含：`dept_id`、`dept_name`、`dept_responsibility`、`manager`（名称）、`members`（名称列表）、`member_count`、`children`

Agent ID 到名称的映射：工具函数需要将 `dept.agent_ids` 和 `dept.manager_id` 转为 Agent 名称。通过 `agentService.get.togo_agents(team_id)` 获取 Agent 列表，建立 `id -> name` 映射表。

```python
team_agents = agentService.get.togo_agents(team_id)
id_to_name = {a.gt_agent.id: a.gt_agent.name for a in.togo_agents}
```

辅助函数 `_serialize_dept_node(node, id_to_name)` 递归序列化部门节点：

```python
def _serialize_dept_node(node: GtDept, id_to_name: dict[int, str]) -> dict:
    members = [id_to_name.get(aid, f"unknown({aid})") for aid in node.agent_ids]
    return {
        "dept_id": node.id,
        "dept_name": node.name,
        "dept_responsibility": node.responsibility,
        "manager": id_to_name.get(node.manager_id, f"unknown({node.manager_id})"),
        "members": members,
        "member_count": len(members),
        "children": [_serialize_dept_node(child, id_to_name) for child in node.children],
    }
```

### 2.3 get_room_info

```python
async def get_room_info(room_name: Optional[str] = None, _context: ToolCallContext = None) -> dict:
    """查询房间信息。不传 room_name 时返回所有房间列表，传入时返回指定房间详情。

    Args:
        room_name: 房间名称，省略时返回所有房间
    """
```

实现要点：

1. 通过 `_context.team_id` 获取 Team ID
2. 列表模式：从 DB 获取 `gtRoomManager.get_rooms_by_team(team_id)`，对每个 room 查找对应的内存 ChatRoom，提取成员名称列表
3. 详情模式：按 `team_id + room_name` 查找 room，返回成员列表、`current_turn`（当前发言者名称）、`total_messages`（消息总数）
4. 房间不存在时返回 `success: false`

成员名称提取：ChatRoom 持有 `agents: dict[int, ...]`，结合 Agent 列表映射为名称。

### 2.4 get_agent_info

```python
async def get_agent_info(agent_name: Optional[str] = None, _context: ToolCallContext = None) -> dict:
    """查询 Agent 信息。不传 agent_name 时返回所有 Agent 状态列表，传入时返回指定 Agent 详情。

    Args:
        agent_name: Agent 名称，省略时返回所有 Agent
    """
```

实现要点：

1. 通过 `agentService.get.togo_agents(team_id)` 获取同 Team 所有内存 Agent
2. **列表模式**：遍历所有 Agent，返回 `name`、`status`（`agent.status.name`）、`department`（部门名称）；`FAILED` 状态额外返回 `error_summary`
3. **详情模式**：按名称匹配 Agent，额外返回 `role`（manager/member）、`rooms`（所在房间名称列表）；`FAILED` 时返回 `error_summary` 和 `can_wake_up: true`
4. Agent 不存在时返回 `success: false`

状态获取：`agent.status` 返回 `AgentStatus` 枚举（`ACTIVE` / `IDLE` / `FAILED`）。

部门归属：调用 `deptService.get_agent_dept(team_id, agent_id)` 获取所在部门；若返回 None 则标记为休闲（off_board）。

错误摘要：`FAILED` 状态的 Agent 其最早未完成任务含 `error_message` 字段，取前 100 字符作为 `error_summary`。通过 `gtAgentTaskManager.get_first_unfinish_task(agent_id)` 获取。

房间列表：遍历 `roomService.get_team_rooms(team_id)`，检查 `room.agents` 是否包含目标 `agent_id`。

### 2.5 wake_up_agent

```python
async def wake_up_agent(agent_name: str, _context: ToolCallContext = None) -> dict:
    """唤醒处于 FAILED 状态的 Agent，使其重新进入调度循环。

    Args:
        agent_name: 要唤醒的 Agent 名称
    """
```

实现要点：

1. 通过 `agentService.get.togo_agents(team_id)` 按名称查找目标 Agent
2. Agent 不存在：返回 `success: false`
3. Agent 状态不是 `FAILED`：返回 `success: false`，附带当前状态说明
4. 调用 `agent.resume_failed()`
5. 成功后返回 `success: true`

**异常处理**：`resume_failed()` 内部使用 `assertUtil` 断言，若断言失败会抛出 `TogoException`。工具函数需 catch 该异常并转为 `success: false` 返回，避免异常传播到 `funcToolService.run_tool_call()` 的通用 catch 块（通用 catch 的错误信息格式不够友好）。

```python
try:
    await target_agent.resume_failed()
    return {"success": True, "message": f"已成功唤醒 {agent_name}，该成员将重新进入调度循环。"}
except Exception as e:
    return {"success": False, "message": f"唤醒 {agent_name} 失败: {e}"}
```

---

## 3. 工具注册

在 `tools.py` 的 `FUNCTION_REGISTRY` 中注册新工具：

```python
FUNCTION_REGISTRY: dict[str, Callable[..., dict] | Callable[..., object]] = {
    # 保留工具
    "get_time": get_time,
    "send_chat_msg": send_chat_msg,
    "finish_chat_turn": finish_chat_turn,
    # V14 团队感知工具
    "get_dept_info": get_dept_info,
    "get_room_info": get_room_info,
    "get_agent_info": get_agent_info,
    "wake_up_agent": wake_up_agent,
}
```

`toolLoader.build_tools()` 会自动从函数签名和 docstring 提取 JSON Schema，`_context` 参数因 `_` 前缀被自动过滤，不暴露给 LLM。Optional 参数自动标记为非必填。

---

## 4. Prompt 增强

### 4.1 工具使用引导注入

在 `promptBuilder.build_agent_system_prompt()` 中追加团队感知工具的使用引导段落。注入条件：`team_id > 0`（即 Agent 属于某个 Team）。

```python
async def build_agent_system_prompt(...) -> str:
    ...
    if team_id > 0:
        dept_context = await _build_dept_context(team_id, agent_name)
        full_prompt += "\n\n" + dept_context
        full_prompt += "\n\n" + _TEAM_AWARENESS_TOOLS_GUIDE  # V14 新增
    return full_prompt
```

### 4.2 引导文本

```python
_TEAM_AWARENESS_TOOLS_GUIDE = """你可以使用以下工具来感知团队状态并协助同伴：
- get_dept_info：了解团队或指定部门的概况与组织架构
- get_room_info：了解房间列表或指定房间详情
- get_agent_info：查看所有同伴状态或指定同伴详细信息
- wake_up_agent：唤醒失败的同伴

当你发现有同伴长时间无响应或对话异常中断时，建议先用 get_agent_info 查看其状态，若为 FAILED 可尝试用 wake_up_agent 唤醒。"""
```

引导文本为静态常量，不依赖运行时状态。

---

## 5. 安全约束实现

| 约束 | 实现方式 |
|------|---------|
| **Team 隔离** | 所有查询以 `_context.team_id` 为范围，service 层方法本身就按 team_id 隔离 |
| **FAILED 前置检查** | `wake_up_agent` 在调用 `resume_failed()` 前检查 `agent.status == AgentStatus.FAILED` |
| **不可自唤醒** | 无需显式检查：Agent 处于 FAILED 时已停止消费，不会执行 tool call |
| **幂等安全** | 对非 FAILED 状态返回 `success: false` + 友好提示，不抛异常 |

---

## 6. 现有代码影响分析

### 6.1 需要修改的文件

| 文件 | 变更内容 |
|------|---------|
| `src/service/funcToolService/tools.py` | 新增 4 个工具函数 + 辅助函数，扩展 `FUNCTION_REGISTRY` |
| `src/service/agentService/promptBuilder.py` | 追加团队感知工具引导段落 |

### 6.2 不受影响的模块

| 模块 | 说明 |
|------|------|
| `funcToolService/core.py` | 无需修改，`startup()` 自动加载新注册的工具 |
| `funcToolService/toolLoader.py` | 无需修改，自动从签名生成 JSON Schema |
| `agentService/toolRegistry.py` | 无需修改，`execute_tool_call()` 自动分发到新工具 |
| `agentService/agentTurnRunner.py` | 无需修改，tool_call 活动记录自动覆盖 |
| `deptService.py` | 只读调用，无需修改 |
| `roomService.py` | 只读调用，无需修改 |
| `controller/agentController.py` | `AgentResumeHandler` 继续独立存在，无需修改 |
| `route.py` | 不新增路由 |

### 6.3 动态 import 约束

为避免形成 `agentService -> funcToolService -> tools.py -> agentService/deptService` 的循环导入，V14 工具实现采用以下约束：

- `tools.py` 顶层不直接引入 `agentService`、`deptService`
- 这些依赖统一放到具体工具函数内部做动态 import
- `gtAgentTaskManager` 若仅被 `get_agent_info` 使用，也放在函数内部导入

示例：

```python
async def get_agent_info(...):
    from service import agentService, deptService
    from dal.db import gtAgentTaskManager
    ...


async def wake_up_agent(...):
    from service import agentService
    ...
```

说明：

- 这是当前阶段的临时方案，用于快速落地 V14
- 长期方案再考虑抽离独立的 facade/query service，彻底消除工具层对具体 service 的直接依赖

---

## 7. 测试策略

### 7.1 测试级别

V14 工具为纯 service 层函数调用，使用**单元测试**（`tests/unit/`）为主，mock 底层 service/DAL 依赖。

### 7.2 测试文件

新增 `tests/unit/test_func_tool_service/test_team_awareness_tools.py`。

### 7.3 测试用例列表

**get_dept_info**：

| 测试方法 | 覆盖场景 |
|----------|---------|
| `test_get_dept_info_root` | 无参数返回根部门及子树 |
| `test_get_dept_info_by_id` | 指定 dept_id 返回对应部门 |
| `test_get_dept_info_not_found` | dept_id 不存在返回 success: false |
| `test_get_dept_info_no_context` | 无 _context 时返回空 |
| `test_get_dept_info_no_dept_tree` | Team 无部门树时返回提示信息 |

**get_room_info**：

| 测试方法 | 覆盖场景 |
|----------|---------|
| `test_get_room_info_list` | 无参数返回所有房间列表 |
| `test_get_room_info_detail` | 传 room_name 返回详情 |
| `test_get_room_info_not_found` | 房间不存在返回 success: false |
| `test_get_room_info_no_context` | 无 _context 时返回空 |

**get_agent_info**：

| 测试方法 | 覆盖场景 |
|----------|---------|
| `test_get_agent_info_list` | 无参数返回所有 Agent 状态列表 |
| `test_get_agent_info_list_includes_self` | 列表模式包含调用者自身 |
| `test_get_agent_info_detail` | 传 agent_name 返回详情 |
| `test_get_agent_info_failed_detail` | FAILED Agent 返回 error_summary 和 can_wake_up |
| `test_get_agent_info_not_found` | 名称不存在返回 success: false |

**wake_up_agent**：

| 测试方法 | 覆盖场景 |
|----------|---------|
| `test_wake_up_success` | FAILED Agent 成功唤醒 |
| `test_wake_up_not_failed` | 非 FAILED 状态返回 success: false |
| `test_wake_up_not_found` | Agent 不存在返回 success: false |
| `test_wake_up_resume_exception` | resume_failed() 抛异常时返回 success: false |

### 7.4 Mock 策略

- Mock `deptService.get_dept_tree()` 返回预构建的 GtDept 树
- Mock `agentService.get.togo_agents()` 返回预构建的 Agent 列表
- Mock `roomService` 相关函数返回预构建的 ChatRoom
- Mock `gtAgentTaskManager.get_first_unfinish_task()` 返回含 error_message 的 task
- Mock `agent.resume_failed()` 验证被调用

---

## 8. 实施步骤

按依赖顺序执行：

1. **实现辅助函数**：在 `tools.py` 中实现 `_serialize_dept_node()` 等内部辅助函数
2. **实现 4 个工具函数**：`get_dept_info`、`get_room_info`、`get_agent_info`、`wake_up_agent`
3. **注册到 FUNCTION_REGISTRY**：扩展注册表
4. **Prompt 增强**：在 `promptBuilder.py` 中追加工具引导段落
5. **编写单元测试**：覆盖正常路径和错误路径
6. **运行测试**：确保所有测试通过且不影响已有测试
