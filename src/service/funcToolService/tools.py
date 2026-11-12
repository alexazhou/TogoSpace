from __future__ import annotations
from typing import Any, Literal, Optional
import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo

from constants import (
    AgentClearField, ALLOWED_AGENT_CLEAR_FIELDS, ALLOWED_DEPT_CLEAR_FIELDS, ALLOWED_ROOM_CLEAR_FIELDS,
    AgentStatus, ScheduleJobType, CONTAINER_SELF_MOUNT_PATH, CONTAINER_SHARED_MOUNT_PATH,
    ClearField, DeptClearField, DriverType, EmployStatus, ExecEnv, RoleTemplateType, RoomClearField,
    RoomState, RoomType, SpecialAgent, TaskStatus,
)
from exception import TogoException
from dal.db import gtAgentManager, gtRoomManager, gtRoleTemplateManager, gtTeamManager, gtAgentTaskManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtDept import GtDept
from model.dbModel.gtRoleTemplate import GtRoleTemplate
from service.roomService import ToolCallContext
import service.containerService as containerService
import service.memoryService as memoryService
import service.roomService as roomService
import service.skillService as skillService
import service.thirdPartyService as thirdPartyService
from service.funcToolService import mcpTool, webFetchTool
from service.agentService.toolRegistry import validate_tool_allow_specs
from service.agentService.prompts import (
    TODO_TASK_CANCEL_INSTRUCTION,
    TODO_TASK_NO_CANCEL_INSTRUCTION,
)
from service.thirdPartyService.result import ThirdPartySearchResult
from util import configUtil, i18nUtil, workspaceUtil
import toolResult

logger = logging.getLogger(__name__)

# list_tasks 工具层 limit 的默认值：不传时照旧走这个内部默认值（#62 的兼容口径不变），
# 传了则透传并在工具层收敛到 [LIST_TASKS_MIN_LIMIT, LIST_TASKS_MAX_LIMIT]。
# 收敛只发生在工具层——服务层 taskService.list_tasks 必须接受任意 limit，
# 因为 Web 列表接口（agentController.py）按 500 / 1000 取数，收敛会截断页面数据。
LIST_TASKS_DEFAULT_LIMIT = 20
LIST_TASKS_MIN_LIMIT = 1
LIST_TASKS_MAX_LIMIT = 50


def _clamp_list_tasks_limit(limit: Optional[int]) -> int:
    """把工具入参 `limit` 收敛成**实际生效值**（不传 / 超界都落到合法区间内）。

    收敛而不是报错：模型传 10000 不该让整个查询失败，但也不能真去查 10000 条撑爆
    上下文；边界收敛后由返回体的 `limit` 字段回显生效值，模型自己就能看出被收了。
    `total` 不受这里影响（服务层 `count_tasks` 与 `limit` 无关），`total > count` 即被截断。
    """
    if limit is None:
        return LIST_TASKS_DEFAULT_LIMIT
    return max(LIST_TASKS_MIN_LIMIT, min(LIST_TASKS_MAX_LIMIT, limit))

# get_task_detail 事件时间线的默认条数（Agent 侧只看最近几条，更早的用 list_task_events 翻页）。
DEFAULT_EVENT_LIMIT = 3

# Tool 返回值规范
# 所有 tool 函数统一返回 dict，由 funcToolService.run_tool_call 序列化为 JSON 字符串后交给 LLM。
# 必填字段：
#   success: bool  — 操作是否成功
# 可选字段（按情况选用，不强制两者都有）：
#   message: str   — 文本信息（成功提示、错误说明等）
#   <其他字段>     — 结构化数据，字段名与语义一致，如 agents: list


def get_time(timezone: Optional[str] = None) -> dict:
    """获取当前时间，返回格式化时间字符串。

    Args:
        timezone: IANA 时区名，如 "Asia/Shanghai"；省略时使用本地时区
    """
    if timezone:
        try:
            tz = ZoneInfo(timezone)
            now = datetime.datetime.now(tz)
            return {"success": True, "message": f"当前时间（时区 {timezone}）: {now.strftime('%Y-%m-%d %H:%M:%S')}"}
        except Exception:
            return {"success": False, "message": f"未知时区: {timezone}"}
    else:
        now = datetime.datetime.now()
        return {"success": True, "message": f"当前本地时间: {now.strftime('%Y-%m-%d %H:%M:%S')}"}


async def web_search(query: str, _context: ToolCallContext = None) -> ThirdPartySearchResult:
    """通过已配置的三方服务执行网页搜索，并根据 API 返回结果自动提取来源链接。

    Args:
        query: 要搜索的 query，不需要额外添加来源链接提示。
    """
    service_name = thirdPartyService.get_default_search_service()
    return await thirdPartyService.search(service_name, query)


async def webfetch(url: str, _context: ToolCallContext = None) -> dict:
    """读取一个网页并把正文转成 Markdown 返回，用于深入阅读搜索到的页面内容。
    只做一次 HTTP 请求（至多跟随 5 次重定向）拿服务端返回的 HTML 转成 Markdown，
    **不执行 JavaScript**（没有浏览器、不跑页面脚本）：静态渲染的文档站、博客、Wiki
    效果最好；纯客户端渲染的 SPA 站点（React/Vue 首屏靠 JS 挂载）只能拿到空壳，通常
    返回 `fetch_empty_content` —— 遇到这种情况请改用其它信息源，不要反复重试同一个
    地址；不解析 PDF / Office / 图片，这类地址返回 `fetch_unsupported_content_type`。
    返回的 Markdown 可能含残留的导航 / 侧栏文字（`Navigation`、`next`/`previous` 等），
    请自行忽略；正文超过 100000 字符会被截断，末尾会写明原文长度。成功时还有 `url`
    （最终地址，重定向后可能与入参不同）、`content_type`、`original_chars`（截断前
    字符数）三个字段；失败时 `error_code` 取值 `fetch_invalid_url` /
    `fetch_blocked_host` / `fetch_timeout` / `fetch_http_error` / `fetch_too_large` /
    `fetch_empty_content` / `fetch_unsupported_content_type`。

    Args:
        url: 要读取的网页地址，必须是 http 或 https 的绝对地址。
    """
    try:
        return await webFetchTool.fetch_markdown(url)
    except Exception as exc:
        return _tool_error_result(exc)


async def mcp_call(
    target: str,
    action: str = "list",
    tool: Optional[str] = None,
    args: Optional[dict] = None,
    kind: str = "openapi",
    extra: Optional[dict] = None,
    _context: ToolCallContext = None,
) -> dict:
    """调用任意外部 MCP server / OpenAPI spec / GraphQL 服务（mcp2cli 运行时转换）。

    先用 `action=list` 拿到该目标的可调用工具清单（含参数说明），再用 `action=call`
    调其中一个。这样外部服务的几十上百个工具只占「一次 list」的 token，不必每个都
    预先塞成独立工具。`kind` 决定 target 怎么解释：`openapi` 填 spec 文件路径或 URL，
    `mcp` 填 MCP server 的 HTTP/SSE 地址，`mcp_stdio` 填启动该 server 的本地命令，
    `graphql` 填 GraphQL endpoint。返回成功时 `result` 是 mcp2cli 的 JSON 输出
    （list 为命令数组，call 为结果 envelope），`result_type` 标出它是 object / array /
    string 等（远端返回纯文本时 `result_type="string"`，不是调用失败）；`truncated=True`
    时**不下发 `result`**，只给 `raw_excerpt` + `original_chars`，需缩小查询范围后重试。
    失败时 `error_code` 取 `mcp_invalid_kind` / `mcp_invalid_target` / `mcp_missing_tool` /
    `mcp_invalid_action` / `mcp_invalid_extra` / `mcp_unsupported_extra` /
    `mcp_call_timeout` / `mcp2cli_exit` / `mcp2cli_error` / `mcp2cli_empty_output` /
    `mcp2cli_bad_json`，其中 `mcp2cli_exit` 说明参数或目标有误（附 `stderr`），可直接
    按提示改参数重试。

    Args:
        target: spec 文件路径 / MCP server URL / stdio 启动命令 / GraphQL endpoint。
        action: `list` 只列工具，`call` 真调一次。
        tool: action=call 时的工具名（从 list 结果里取）。
        args: action=call 时的参数对象，键名与 list 里该工具的参数名一致。
        kind: 来源类型：`openapi` / `mcp` / `mcp_stdio` / `graphql`，默认 `openapi`。
        extra: 透传 **mcp2cli 全局开关**，如 `{"transport": "sse", "search": "关键词"}`。
            `search` 需与 `action=list` 配合；`head` 只对 `mcp` / `mcp_stdio` / `graphql`
            生效（`kind=openapi` 下上游不传给执行函数，传了会给 `mcp_unsupported_extra`）；
            `raw` / `toon` 因本工具固定带 `--json` 而不生效，同样会被拒。
    """
    return await mcpTool.call_mcp(
        kind=kind,
        target=target,
        action=action,
        tool=tool,
        args=args,
        extra=extra,
    )


def _require_team_context(_context: ToolCallContext | None) -> tuple[bool, int]:
    if _context is None or _context.team_id <= 0:
        return False, 0
    return True, _context.team_id


def _resolve_agent_name(agent_id: int, id_to_name: dict[int, str]) -> str:
    if agent_id == int(SpecialAgent.SYSTEM.value):
        return SpecialAgent.SYSTEM.name
    if agent_id == int(SpecialAgent.OPERATOR.value):
        return SpecialAgent.OPERATOR.name
    return id_to_name.get(agent_id, f"unknown({agent_id})")


def _find_dept_node(node: GtDept | None, dept_id: int) -> GtDept | None:
    if node is None:
        return None
    if node.id == dept_id:
        return node
    for child in node.children:
        found = _find_dept_node(child, dept_id)
        if found is not None:
            return found
    return None


def _find_dept_node_by_name(node: GtDept | None, name: str) -> GtDept | None:
    """在部门树里按**内部标识** name 定位（与 `dept get --name` 同口径，不比对 i18n 展示名）。

    为什么按 `node.name` 而不按 `_serialize_dept_node` 的展示名：定位参数必须是**可回写**
    的标识——展示名取 i18n、会随语言设置变化，拿它定位会出现「换个语言就查不到」。
    """
    if node is None:
        return None
    if node.name == name:
        return node
    for child in node.children:
        found = _find_dept_node_by_name(child, name)
        if found is not None:
            return found
    return None


def _locator_label(dept_id: int | None, name: str | None) -> str:
    """生成「未找到」文案里的定位值片段（draft §2.1：错误文案带实际使用的定位值）。

    同时给出 id 与 name 时以 id 为准，文案也只报 id —— 报两个值会让调用方以为两个都没命中，
    实际只查了 id 那一个。
    """
    if dept_id is not None:
        return f"id={dept_id}"
    return f"name={name}"


def _tool_error_result(exc: BaseException) -> dict[str, Any]:
    """把异常转成工具层的结构化失败返回体。

    为什么不在 `run_tool_call` 一处兜底就完事：工具层自己 `except` 后 return 的分支
    （`save_room` / `delete_room` / `delete_dept`）根本不会经过 `run_tool_call` 的
    兜底 `except`，所以必须在这三处各自转换。

    #189 起这里**不再自己拼**：形状与「取哪段文本、要不要带 error_code」的口径收敛到
    `toolResult.error_result`（唯一实现），本函数只保留名字供既有调用点/用例引用。
    """

    return toolResult.error_result(exc)


def _serialize_dept_node(node: GtDept, id_to_name: dict[int, str]) -> dict[str, Any]:
    lang = configUtil.get_language()
    dept_name = i18nUtil.extract_i18n_str(
        node.i18n.get("dept_name") if node.i18n else None,
        default=node.name,
        lang=lang,
    ) or node.name
    responsibility = i18nUtil.extract_i18n_str(
        node.i18n.get("responsibility") if node.i18n else None,
        default=node.responsibility,
        lang=lang,
    ) or node.responsibility
    members = [_resolve_agent_name(agent_id, id_to_name) for agent_id in node.agent_ids]
    return {
        "dept_id": node.id,
        "dept_name": dept_name,
        "dept_responsibility": responsibility,
        "manager": _resolve_agent_name(node.manager_id, id_to_name),
        "members": members,
        "member_count": len(members),
        "children": [_serialize_dept_node(child, id_to_name) for child in node.children],
    }


async def _build_team_agent_name_map(team_id: int) -> dict[int, str]:
    # 临时优先复用运行态 Agent，拿不到时再回退 DB，避免工具在测试/恢复场景下名称缺失。
    try:
        from service import agentService

        team_agents = agentService.get_team_agents(team_id)
        if team_agents:
            return {agent.gt_agent.id: agent.gt_agent.name for agent in team_agents}
    except Exception:
        logger.debug("build team agent name map from runtime failed, fallback to db", exc_info=True)

    gt_agents = await gtAgentManager.get_team_all_agents(team_id)
    return {agent.id: agent.name for agent in gt_agents}


def _truncate_error_message(message: str | None, limit: int = 100) -> str:
    if not message:
        return ""
    if len(message) <= limit:
        return message
    return message[:limit].rstrip() + "..."


def _normalize_clear_fields(
    clear_fields: Any, allowed: frozenset[str], tool_name: str
) -> tuple[list[str], dict[str, Any] | None]:
    """把 `clear_fields` 收口成去重后的字段名列表；不合法则返回失败返回体。

    口径完全照 `taskService.update_task`（#181/#158 已验证的那一套），不发明新规则：

    1. **类型收口先于归一**：`5` 直接迭代会抛未捕获 `TypeError`（模型看到 Python 堆栈措辞
       而不是错误码）；`dict` 可迭代且会拿到 keys，若 key 恰是合法字段名就会**静默清除**，
       属「输入形态错但意图被猜对」——正是要避免的静默取一边。
       接受 `str`（裸字符串兼容）或元素全为 `str` 的 `list`/`tuple`，其余一律报错。
    2. **白名单校验放在这里**（工具层），而不是只靠签名上的 `Literal`：`Literal` 生成的
       enum 约束由 #189 校验层负责拦「直调/console 转发」两条路径；但工具也可能被直接
       函数调用（测试、内部代码），这层是纵深防御，错误码 `invalid_clear_field`。
    3. **空数组合法**：`[]` = 「没要清任何字段」= 省略，不是错误（与 `list_tasks.status=[]`
       的「意图不明故报错」相反，见 `update_task` docstring 的口径说明）。
    """
    if clear_fields is None:
        return [], None
    if isinstance(clear_fields, str):
        names = [clear_fields]
    elif isinstance(clear_fields, (list, tuple)) and all(isinstance(f, str) for f in clear_fields):
        names = list(clear_fields)
    else:
        return [], {
            "success": False,
            "message": (
                f"clear_fields 必须是字段名数组（{tool_name} 当前可用："
                f"{' / '.join(sorted(allowed)) or '无'}），收到的是 {type(clear_fields).__name__}"
            ),
            "error_code": "invalid_clear_field",
        }

    names = list(dict.fromkeys(names))
    invalid = [f for f in names if f not in allowed]
    if invalid:
        return [], {
            "success": False,
            "message": (
                f"不支持清除的字段：{'、'.join(invalid)}；"
                f"{tool_name} 当前可清除的字段只有 {' / '.join(sorted(allowed)) or '无'}"
            ),
            "error_code": "invalid_clear_field",
        }
    return names, None


def _clear_field_conflict(
    clearing: set[str], provided: dict[str, Any], tool_name: str
) -> dict[str, Any] | None:
    """同一字段既给值又列入 `clear_fields` ⇒ 拒绝。

    两个参数表达相反的意图（设置 vs 清除），静默取一边就是「把猜测当授权」。
    与 `update_task` 的 `ambiguous_reviewer_change` 同口径：有歧义就报错，不猜。
    """
    both = [field for field, value in provided.items() if value is not None and field in clearing]
    if not both:
        return None
    return {
        "success": False,
        "message": (
            f"字段 {'、'.join(both)} 不能同时给值和列入 clear_fields："
            "给值表示设置，clear_fields 表示清除，请二选一。"
        ),
        "error_code": "ambiguous_clear_field",
    }


async def get_dept_info(
    dept_id: Optional[int] = None,
    name: Optional[str] = None,
    _context: ToolCallContext = None,
) -> dict:
    """查询部门，返回部门 ID、名称、职责、负责人、成员列表与子部门树。

    Args:
        dept_id: 部门 ID；与 name 二选一，同时给出时以本参数为准。全部省略返回整个团队部门树。
        name: 部门名称（内部标识，即 name 字段，非多语言展示名）；纯数字名称请用本参数显式指定，避免被当作 ID。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    from service import deptService

    root = await deptService.get_dept_tree(team_id)
    if root is None:
        return {"success": False, "message": "当前团队还没有部门信息。"}

    normalized_name = None if name is None else name.strip()
    if dept_id is not None:
        target = _find_dept_node(root, dept_id)
    elif normalized_name:
        target = _find_dept_node_by_name(root, normalized_name)
    elif normalized_name == "":
        # 显式传空串不等于「省略」：省略是「返回全树」，空串是一个不存在的名字。
        return {"success": False, "message": "未找到部门: name="}
    else:
        target = root
    if target is None:
        return {"success": False, "message": f"未找到部门: {_locator_label(dept_id, normalized_name)}"}

    id_to_name = await _build_team_agent_name_map(team_id)
    return {"success": True, "dept": _serialize_dept_node(target, id_to_name)}


async def get_room_info(
    room_id: Optional[int] = None,
    name: Optional[str] = None,
    _context: ToolCallContext = None,
) -> dict:
    """查询房间，返回房间名、类型、状态、成员与标签；详情另含当前轮次与消息数。

    Args:
        room_id: 房间 ID；与 name 二选一，同时给出时以本参数为准。全部省略返回团队全部房间的列表。
        name: 房间名称；纯数字名称请用本参数显式指定，避免被当作 ID。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    id_to_name = await _build_team_agent_name_map(team_id)

    normalized_name = None if name is None else name.strip()

    if room_id is None and normalized_name is None:
        room_configs = await gtRoomManager.get_rooms_by_team(team_id)
        rooms: list[dict[str, Any]] = []
        for room_config in room_configs:
            runtime_room = roomService.get_room(room_config.id)
            rooms.append({
                "room_name": room_config.name,
                "room_type": room_config.type.name,
                "state": runtime_room.state.name if runtime_room is not None else RoomState.INIT.name,
                "members": [
                    _resolve_agent_name(agent_id, id_to_name)
                    for agent_id in (room_config.agent_ids or [])
                    if agent_id != int(SpecialAgent.SYSTEM.value)
                ],
                "member_count": len([
                    agent_id
                    for agent_id in (room_config.agent_ids or [])
                    if agent_id != int(SpecialAgent.SYSTEM.value)
                ]),
                "tags": list(room_config.tags or []),
            })
        return {"success": True, "rooms": rooms}

    if room_id is not None:
        room_config = await gtRoomManager.get_room_by_id(room_id)
        if room_config is not None and room_config.team_id != team_id:
            room_config = None
    elif normalized_name == "":
        # 显式传空串不等于「省略」：省略是「返回全部房间」，空串是一个不存在的名字。
        return {"success": False, "message": "未找到房间: name="}
    else:
        room_config = await gtRoomManager.get_room_by_team_and_name(team_id, normalized_name)
    if room_config is None:
        return {"success": False, "message": f"未找到房间: {_locator_label(room_id, normalized_name)}"}

    runtime_room = roomService.get_room(room_config.id)
    room_dict: dict[str, Any] = {
        "room_name": room_config.name,
        "room_type": room_config.type.name,
        "state": runtime_room.state.name if runtime_room is not None else RoomState.INIT.name,
        "members": [
            _resolve_agent_name(agent_id, id_to_name)
            for agent_id in (room_config.agent_ids or [])
            if agent_id != int(SpecialAgent.SYSTEM.value)
        ],
        "member_count": len([
            agent_id
            for agent_id in (room_config.agent_ids or [])
            if agent_id != int(SpecialAgent.SYSTEM.value)
        ]),
        "current_turn": _resolve_agent_name(runtime_room.get_current_turn_agent_id(), id_to_name) if runtime_room is not None and runtime_room.state == RoomState.SCHEDULING else None,
        "total_messages": len(runtime_room.messages) if runtime_room is not None else 0,
        "tags": list(room_config.tags or []),
    }
    return {"success": True, "room": room_dict}


async def start_chat(
    agent_name: str,
    _context: ToolCallContext = None,
) -> dict:
    """与指定 Agent 发起单聊（私聊）。若两人之间已有房间则直接返回，不重复创建。

    Args:
        agent_name: 要发起对话的目标 Agent 名称（内部标识符，即 name 字段，非 display_name）。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    if _context is None or not _context.agent_id:
        return {"success": False, "message": "无法获取当前 Agent 身份。"}

    normalized = agent_name.strip()
    if not normalized:
        return {"success": False, "message": "目标 Agent 名称不能为空。"}

    all_agents = await gtAgentManager.get_team_all_agents(team_id)
    name_to_agent: dict[str, Any] = {a.name: a for a in all_agents}
    id_to_name: dict[int, str] = {a.id: a.name for a in all_agents}

    target = name_to_agent.get(normalized)
    if target is None:
        return {"success": False, "message": f"未找到成员: {normalized}"}

    self_id = _context.agent_id
    if target.id == self_id:
        return {"success": False, "message": "不能与自己发起单聊。"}

    member_ids = [self_id, target.id]
    member_set = set(member_ids)

    # 若已存在成员相同的房间，直接返回
    existing_rooms = await gtRoomManager.get_rooms_by_team(team_id)
    for existing_room in existing_rooms:
        if set(existing_room.agent_ids or []) == member_set:
            if roomService.get_room(existing_room.id) is None:
                await roomService.load_and_activate_room(existing_room.id)
            return {
                "success": True,
                "message": f"已存在与 {normalized} 的单聊房间 {existing_room.name}，无需重复创建。",
                "room": {
                    "room_id": existing_room.id,
                    "name": existing_room.name,
                    "members": [id_to_name.get(aid, str(aid)) for aid in (existing_room.agent_ids or [])],
                },
                "is_new_created": False,
            }

    # 按名称字母序生成房间名，保证唯一且可预测
    self_name = id_to_name.get(self_id, str(self_id))
    room_name = "_".join(sorted([self_name, normalized]))

    saved = await roomService.create_room(
        team_id=team_id,
        name=room_name,
        agent_ids=member_ids,
    )

    return {
        "success": True,
        "message": f"已创建与 {normalized} 的单聊房间 {saved.name}。",
        "room": {
            "room_id": saved.id,
            "name": saved.name,
            "members": [id_to_name.get(aid, str(aid)) for aid in (saved.agent_ids or [])],
        },
        "is_new_created": True,
    }


async def get_agent_info(
    agent_id: Optional[int] = None,
    name: Optional[str] = None,
    _context: ToolCallContext = None,
) -> dict:
    """查询成员，返回 ID、name、display_name、状态与所在部门；详情另含房间与可唤醒状态。

    Args:
        agent_id: 成员 ID；与 name 二选一，同时给出时以本参数为准。全部省略返回团队成员列表。
        name: Agent 名称（内部标识符 name，也接受 display_name）；纯数字名称请用本参数显式指定，避免被当作 ID。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    from service import agentService, deptService
    from dal.db import gtScheduleJobManager

    team_agents = agentService.get_team_agents(team_id)

    async def _build_agent_dict(agent: Any, *, detail: bool) -> dict[str, Any]:
        agent_id = agent.gt_agent.id
        depts_with_paths: list[tuple[GtDept, str]] = await deptService.get_agent_depts(team_id, agent_id)
        first_task = await gtScheduleJobManager.get_first_unfinished_job(agent_id) if agent.status == AgentStatus.FAILED else None
        info: dict[str, Any] = {
            "id": agent_id,
            "name": agent.gt_agent.name,
            "display_name": agent.gt_agent.display_name,
            "status": agent.status.name,
            "departments": [
                {
                    "name": path,
                    "position": "manager" if dept.manager_id == agent_id else "member"
                }
                for dept, path in depts_with_paths
            ] if depts_with_paths else None,
        }
        if first_task is not None:
            info["error_summary"] = _truncate_error_message(first_task.error_message)
        if detail:
            info["rooms"] = [
                room.name
                for room in roomService.get_all_rooms()
                if room.team_id == team_id and agent_id in room.get_agent_ids()
            ]
            info["can_wake_up"] = agent.status == AgentStatus.FAILED
        return info

    normalized_name = None if name is None else name.strip()

    if agent_id is None and normalized_name is None:
        agents = [await _build_agent_dict(agent, detail=False) for agent in team_agents]
        return {"success": True, "agents": agents}

    if agent_id is not None:
        target_agent = next((agent for agent in team_agents if agent.gt_agent.id == agent_id), None)
        if target_agent is None:
            # 按 ID 定位时，「查不到」有三种完全不同的原因，必须分开报：
            # ① 库里根本没有这个 ID（真未找到）；
            # ② 库里有、但已离职（`agentService` 只加载 ON_BOARD）⇒ 详情里的 rooms / 状态
            #    这些运行态信息取不到；
            # ③ 库里有、在职，但运行时尚未加载（刚建/刚改、还没 reload）⇒ 同样取不到运行态。
            # 把 ②③ 说成「未找到成员」都是误导：用户的实体明明存在，该做的是 reload。
            row = await gtAgentManager.get_agent_by_id(agent_id)
            if row is None or row.team_id != team_id:
                return {"success": False, "message": f"未找到成员: {_locator_label(agent_id, normalized_name)}"}
            if row.employ_status != EmployStatus.ON_BOARD:
                return {
                    "success": False,
                    "error_code": "member_not_on_board",
                    "message": (
                        f"成员 {row.name}（id={row.id}）当前不在职（{row.employ_status.name}），"
                        "未加载到运行时，无法返回房间与状态详情。若刚刚创建/修改，需要 reload 后才生效。"
                    ),
                }
            return {
                "success": False,
                "error_code": "agent_runtime_not_loaded",
                "message": (
                    f"成员 {row.name}（id={row.id}）的配置已落库，但运行时尚未加载，"
                    "无法返回房间与状态详情。请先 reload_team 后重试。"
                ),
            }
    elif normalized_name == "":
        # 显式传空串不等于「省略」：省略是「返回全部成员」，空串是一个不存在的名字。
        return {"success": False, "message": "未找到成员: name="}
    else:
        target_agent = next(
            (
                agent
                for agent in team_agents
                if agent.gt_agent.name == normalized_name or agent.gt_agent.display_name == normalized_name
            ),
            None,
        )
        if target_agent is None:
            return {"success": False, "message": f"未找到成员: {_locator_label(None, normalized_name)}"}

    return {"success": True, "agent": await _build_agent_dict(target_agent, detail=True)}


async def wake_up_agent(agent_name: str, _context: ToolCallContext = None) -> dict:
    """唤醒处于 FAILED 状态的 Agent，使其重新进入调度循环。

    Args:
        agent_name: 要唤醒的 Agent 名称（内部标识符，即 name 字段，非 display_name）
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    from service import agentService

    team_agents = agentService.get_team_agents(team_id)
    target_agent = next((agent for agent in team_agents if agent.gt_agent.name == agent_name), None)
    if target_agent is None:
        return {"success": False, "message": f"未找到成员: {agent_name}"}

    if target_agent.status != AgentStatus.FAILED:
        return {"success": False, "message": f"{agent_name} 当前状态为 {target_agent.status.name}，无需唤醒。"}

    target_agent.start_consumer_task()
    return {"success": True, "message": f"已成功唤醒 {agent_name}，该成员将重新进入调度循环。"}


async def reload_team(_context: ToolCallContext = None) -> dict:
    """重载当前团队的运行时，可能中断团队内正在执行的任务。
    成员、组织或房间的配置变更需重载后才生效。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    from service import teamService

    team = await gtTeamManager.get_team_by_id(team_id)
    if team is None:
        return {"success": False, "message": f"未找到团队: team_id={team_id}"}

    # 在独立 task 里执行 hot_reload，使其不受当前 consumer task 取消的影响。
    # hot_reload 内部会调用 stop_team_runtime（取消当前 consumer），
    # 若直接 await，stop_team_runtime 的取消信号会打断自身，导致 restore_team 永远无法执行。
    asyncio.create_task(teamService.hot_reload_team(team.name))

    # 等待被 stop_team_runtime 取消，代码正常情况下不会走到 return。
    # 真正的成功结果由重启后的自中断恢复逻辑（self_interrupt）写入。
    await asyncio.get_event_loop().create_future()

    return {"success": False, "message": f"团队 {team.name} 重载已触发，等待 agent 重启后确认。"}

async def list_role_templates(keywords: list[str] | None = None, _context: ToolCallContext = None) -> dict:
    """查询全部角色模板列表。
    返回精简字段，不包含 soul；display_name 为当前语言下的名称。

    Args:
        keywords: 可选，关键词搜索列表。若提供，则仅返回名称或 soul 中包含这些词的模板。
    """
    if keywords:
        templates = await gtRoleTemplateManager.search_role_templates(keywords)
    else:
        templates = await gtRoleTemplateManager.get_all_role_templates()

    # 转换为 JSON 字典并剔除 soul 以节省 Token
    role_templates = []

    for t in templates:
        data = t.to_json()
        data.pop("soul", None)
        role_templates.append(data)

    return {
        "success": True,
        "role_templates": role_templates,
    }


async def get_role_template(role_name: str, _context: ToolCallContext = None) -> dict:
    """按名称查询单个角色模板详情。

    Args:
        role_name: 角色模板名称
    """
    template = await gtRoleTemplateManager.get_role_template_by_name(role_name.strip())
    if template is None:
        return {"success": False, "message": f"未找到角色模板: {role_name}"}
    return {"success": True, "role_template": template.to_json()}


async def save_role_template(
    name: str,
    type: Optional[str] = None,
    soul: Optional[str] = None,
    i18n: dict | None = None,
    overwrite: bool = False,
    _context: ToolCallContext = None,
) -> dict:
    """创建或更新角色模板；只能操作 USER 类型，SYSTEM 内置模板只读不可改。

    ⚠️ **更新已有模板时，省略某个可选字段 = 保持该字段现值**（PATCH 语义）：
    type / soul / i18n 都遵守。新建时 type 省略默认 USER、i18n 省略默认 {}，
    soul 必须提供。

    Args:
        name: 角色模板名称，唯一标识，建议使用小写字母和下划线。
        type: 角色模板类型，只允许 USER；传 SYSTEM 会被拒绝（内置模板只读）。新建时省略默认 USER，更新时省略表示保持现值。
        soul: 核心提示词，应包含身份定位、职责边界与行为准则；会作为核心指令注入该角色的 System Prompt。新建时必填，更新时省略表示保持现值。
        i18n: 多语言名称，如 {"display_name": {"zh-CN": "高级写手", "en": "Senior Writer"}}。新建时省略为 {}，更新时省略表示保持现值。
        overwrite: 更新已有同名模板**必须**设为 true；默认 false 只允许新建。这是防止误覆盖已有模板的保护开关。
    """
    from service import roleTemplateService

    normalized_name = name.strip()
    if not normalized_name:
        return {"success": False, "message": "角色模板名称不能为空。"}

    role_type = None
    if type is not None:
        role_type = RoleTemplateType.value_of(type)
        if role_type is None:
            return {"success": False, "message": "角色模板 type 只允许 SYSTEM 或 USER。"}

    existing = await gtRoleTemplateManager.get_role_template_by_name(normalized_name)
    if existing is None and role_type == RoleTemplateType.SYSTEM:
        return {"success": False, "message": "SYSTEM 角色模板不允许通过工具创建。"}
    if existing is not None and existing.type == RoleTemplateType.SYSTEM:
        return {"success": False, "message": f"SYSTEM 角色模板 {normalized_name} 不允许通过工具修改。"}
    if existing is not None and role_type == RoleTemplateType.SYSTEM:
        # 只拦「已是 SYSTEM」还不够：USER -> SYSTEM 同样是写入内置语义，
        # 且会让模板从此不可再被本工具修改，必须一并拒绝。
        return {
            "success": False,
            "message": f"不允许将角色模板 {normalized_name} 的类型从 USER 改为 SYSTEM。",
        }
    if existing is not None and overwrite is not True:
        return {
            "success": False,
            "message": f"角色模板 {normalized_name} 已存在；如需覆盖请将 overwrite 设为 true。",
        }

    if existing is None:
        if soul is None:
            return {
                "success": False,
                "message": "创建角色模板时必须提供 soul。",
                "error_code": toolResult.INVALID_TOOL_ARGS,
            }
        resolved_type = role_type or RoleTemplateType.USER
        resolved_soul = soul
        resolved_i18n = i18n if i18n is not None else {}
    else:
        # PATCH 语义：省略（None）= 保持现值。i18n 不能写 `i18n or {}`——
        # 那会在「只改 soul」时把已有的多语言名称清空。
        resolved_type = role_type if role_type is not None else existing.type
        resolved_soul = soul if soul is not None else existing.soul
        resolved_i18n = i18n if i18n is not None else existing.i18n

    saved = await roleTemplateService.save_role_template(
        GtRoleTemplate(
            name=normalized_name,
            soul=resolved_soul,
            type=resolved_type,
            i18n=resolved_i18n,
        )
    )
    action = "更新" if existing is not None else "创建"
    return {
        "success": True,
        "message": f"已{action}角色模板 {normalized_name}。",
        "role_template": saved.to_json(),
    }


async def save_agent(
    name: Optional[str] = None,
    role_template_name: Optional[str] = None,
    model: str | None = None,
    driver: Optional[str] = None,
    allow_tools: list[str] | None = None,
    allow_skills: list[str] | None = None,
    i18n: dict | None = None,
    overwrite: bool = False,
    agent_id: int | None = None,
    clear_fields: Optional[list[AgentClearField]] = None,
    _context: ToolCallContext = None,
) -> dict:
    """在当前团队中创建或更新成员。

    ⚠️ **更新已有成员时，省略某个可选字段 = 保持该字段现值**（不是重置为默认）。
    要把字段恢复成「没有这层覆盖」，用 clear_fields 显式声明。这是 #196 的行为变更：
    改造前「省略即重置为默认」，现改为 PATCH 语义——因为省略与「没打算改」在调用方
    视角下无法区分，按重置处理会让一次只想改 model 的调用顺手清掉 allow_tools。

    Args:
        name: 成员名称，用于定位或新建；不能使用系统保留名称，也不能包含路径分隔符、Windows 非法字符或控制字符。新建成员时必须给；更新时可省略（用 agent_id 定位）。按名称定位时若团队内存在多行同名记录（含离职成员），会返回 ambiguous_target，请改用 agent_id。
        role_template_name: 要绑定的角色模板名称。新建成员时必须给；更新时省略表示保持现有绑定。
        model: 可选模型覆盖；新建时省略表示不覆盖模板或系统默认模型，更新时省略表示保持现值。
        driver: 驱动类型，可选 native、claude_sdk、tsp。新建时省略 = tsp。**不可通过 clear_fields 清除**（无「无驱动」这一合法状态）。更新时省略表示保持现值。
        allow_tools: 可见工具列表，元素为工具名（如 "read_file"）或类别语法（如 "Category:Read"），类别与工具名自动合并。可分配类别只 Read、Write、Execute 三类（Basic 自动开启、Admin 拒绝显式分配，根主管自动获得 Admin）。新建时省略 = 默认授予 Basic、Read、Write、Execute；更新时省略 = 保持现值。clear_fields 含本字段 = 重置为默认授予。
        allow_skills: 可见技能名称列表，如 ["frontend-design"]；load_skill 只能加载已授权的技能。新建时省略 = 全部技能，[] = 不授权任何技能；更新时省略 = 保持现值。clear_fields 含本字段 = 重置为默认（全部技能）。
        i18n: 多语言名称，如 {"display_name": {"zh-CN": "Alice", "en": "Alice"}}。更新时省略表示保持现值。
        overwrite: 更新已有成员**必须**设为 true（无论按 name 还是 agent_id 定位）；默认 false 只允许新建。这是防止误覆盖已有成员的保护开关。
        agent_id: 成员 ID；传入后按 ID 精确定位，此时 name 用于重命名。
        clear_fields: 要清除（恢复默认）的字段名列表，可选 "model"、"allow_tools"、"allow_skills"。传空数组 [] 视为「未提供」。与给值同时出现同一字段会报 ambiguous_clear_field。白名单之外的字段名返回 invalid_clear_field。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    if agent_id is None and name is None:
        return {
            "success": False,
            "message": "请用 agent_id 或 name 指定成员；新建成员必须给 name。",
            "error_code": toolResult.INVALID_TOOL_ARGS,
        }

    normalized_name: str | None = None
    if name is not None:
        normalized_name = name.strip()
        if not normalized_name:
            return {"success": False, "message": "成员名称不能为空。"}

        name_error = workspaceUtil.validate_dir_name(normalized_name)
        if name_error is not None:
            return {"success": False, "message": name_error}

        special_agent = SpecialAgent.value_of(normalized_name)
        if special_agent is not None:
            return {"success": False, "message": f"保留成员 {special_agent.name} 不允许通过工具创建或修改。"}

    normalized_role_template_name = None if role_template_name is None else role_template_name.strip()
    if normalized_role_template_name is not None and not normalized_role_template_name:
        return {"success": False, "message": "角色模板名称不能为空。"}

    # driver 是「给了就校验」：省略时按参数默认值 tsp 走，不进「保持现值」分支——
    # driver 在 draft §2.2 里明确不可 clear，也没有「无驱动」状态，故它不属于 PATCH 面。
    #
    # ⚠️ 为了让「省略」真的可表达，签名默认值必须是 `None`（不是 `DriverType.TSP.value`）：
    # 写成 `driver: str = "tsp"` 时，更新分支无法区分「调用方没传」与「调用方传了 tsp」，
    # 结果是「只改 model 的一次 PATCH」会把 driver 从 native 悄悄改回 tsp。
    # 新建分支仍需落到 tsp，由下面 `existing is None` 那条补。
    driver_supplied = driver is not None
    driver_type = DriverType.value_of(driver or DriverType.TSP.value)
    if driver_type is None:
        return {"success": False, "message": "成员 driver 只允许 native、claude_sdk 或 tsp。"}

    error_msg = validate_tool_allow_specs(allow_tools or [])
    if error_msg is not None:
        return {"success": False, "message": error_msg}

    clear_names, clear_error = _normalize_clear_fields(
        clear_fields, ALLOWED_AGENT_CLEAR_FIELDS, "save_agent"
    )
    if clear_error is not None:
        return clear_error
    clearing = set(clear_names)
    conflict = _clear_field_conflict(
        clearing,
        {"model": model, "allow_tools": allow_tools, "allow_skills": allow_skills},
        "save_agent",
    )
    if conflict is not None:
        return conflict

    if agent_id is not None:
        existing = await gtAgentManager.get_agent_by_id(agent_id)
        if existing is None or existing.team_id != team_id:
            return {"success": False, "message": f"未找到 ID 为 {agent_id} 的成员。"}
    else:
        # 无 agent_id 时 name 就是定位键，上面已确保它存在。
        existing = await gtAgentManager.get_agent(team_id, normalized_name, status=None)
        if existing is not None:
            # 同名消歧必须在这里做：`GtAgent` 的 `(team_id, name)` 是**非唯一**索引
            # （离职成员的名字允许被复用），而 `get_agent` 底层 `aio_get` 只取第一行、
            # 不校验唯一性。按名更新会静默改到离职成员，返回体仍是 success=True。
            # 之前这段保护在 `consoleParsing._resolve_agent_by_target` 里，只有 console
            # 走得到；下沉到工具层后，直接调用 `save_agent(name=...)` 也受同一保护。
            # 只在「已存在」时查全量：新建时同名行数为 0，多查一次没有意义。
            same_name = [
                agent
                for agent in await gtAgentManager.get_team_all_agents(team_id)
                if agent.name == normalized_name
            ]
            if len(same_name) > 1:
                return {
                    "success": False,
                    "error_code": "ambiguous_target",
                    "message": (
                        f"团队内有 {len(same_name)} 个同名成员 {normalized_name}（含离职成员）；"
                        "请改用 agent_id 定位。"
                    ),
                    "candidates": [
                        {"id": agent.id, "name": agent.name, "employ_status": agent.employ_status.name}
                        for agent in same_name
                    ],
                }

    # 更新保护对**两种定位方式一视同仁**：按 ID 也是更新已有记录，必须显式
    # overwrite=true 确认，否则「只想改 model 的调用」可能悄无声息地覆盖整行。
    # 放在同名消歧之后：多行同名时 ambiguous_target 更具体，先报它。
    if existing is not None and overwrite is not True:
        return {
            "success": False,
            "message": f"成员 {existing.name} 已存在；如需覆盖请将 overwrite 设为 true。",
        }

    if existing is not None and existing.team_id == -1:
        return {"success": False, "message": f"保留成员 {existing.name} 不允许通过工具创建或修改。"}

    # PATCH 语义：只给 agent_id 时 name 省略 = 不改名。回显/日志/改名判定都要有名字，
    # 故这里补上现值（与 `save_dept` / `save_room` 同口径）——否则返回体是
    # 「已更新成员 None」，脏文案会经 console 直达模型。
    if existing is not None and normalized_name is None:
        normalized_name = existing.name

    if existing is None and normalized_name is None:
        return {
            "success": False,
            "message": "新建成员必须给 name。",
            "error_code": toolResult.INVALID_TOOL_ARGS,
        }

    # 新建时的必填项在运行时补齐（schema 层已放开，见 docstring 上方说明）。
    if existing is None and normalized_role_template_name is None:
        return {
            "success": False,
            "message": "新建成员时必须指定 role_template_name（角色模板名称）。",
            "error_code": toolResult.INVALID_TOOL_ARGS,
        }

    role_template = None
    if normalized_role_template_name is not None:
        role_template = await gtRoleTemplateManager.get_role_template_by_name(normalized_role_template_name)
        if role_template is None:
            return {"success": False, "message": f"未找到角色模板: {normalized_role_template_name}"}

    old_name = existing.name if existing is not None else ""
    agent = existing or GtAgent(
        team_id=team_id,
        name=normalized_name,
        employ_status=EmployStatus.OFF_BOARD,
    )
    # 更新分支的 PATCH 语义：省略（None）= 保持现值；显式列入 clear_fields = 恢复默认。
    # 新建分支没有「现值」可保持，省略即取默认，与改造前一致。
    if normalized_name is not None:
        agent.name = normalized_name
    if role_template is not None:
        agent.role_template_id = role_template.id
    if "model" in clearing:
        # 模型覆盖没有独立列，「清除」的落库形态就是空串（等价于「未覆盖」）。
        agent.model = ""
    elif model is not None:
        agent.model = model
    elif existing is None:
        agent.model = ""
    if driver_supplied:
        agent.driver = driver_type
    elif existing is None:
        agent.driver = driver_type
    if "allow_tools" in clearing:
        agent.allow_tools = None
    elif allow_tools is not None:
        agent.allow_tools = allow_tools
    elif existing is None:
        agent.allow_tools = allow_tools
    if "allow_skills" in clearing:
        agent.allow_skills = None
    elif allow_skills is not None:
        agent.allow_skills = allow_skills
    elif existing is None:
        agent.allow_skills = allow_skills
    if i18n is not None:
        agent.i18n = i18n
    elif existing is None:
        agent.i18n = {}

    # 改名时同步迁移私有目录；写库失败则把目录改回去
    from service.agentService.core import rename_agent_workdir, switch_agent_to_host
    moved_dirs: list[tuple[str, str]] = []
    # ⚠️ `normalized_name is not None` 这个前置条件不能省：更新时省略 name 就是
    # `normalized_name = None`，若只判 `old_name != normalized_name`，"alice" != None
    # 恒成立 ⇒ 每次「只改 model」的 PATCH 都会被当成改名为空，触发目录迁移/删容器。
    renaming = (
        existing is not None
        and normalized_name is not None
        and old_name != normalized_name
    )
    if renaming:
        # 容器的 bind mount 固化了旧目录的绝对路径，必须在移动目录前清掉旧容器（§7）。
        await switch_agent_to_host(existing.id)
        await containerService.delete_agent_containers_safely(existing.id)
        moved_dirs = await rename_agent_workdir(team_id, old_name, normalized_name)
    try:
        await gtAgentManager.batch_save_agents(team_id, [agent])
    except Exception:
        if moved_dirs:
            # 回滚只恢复目录；容器已在改名时清掉，不重复删除
            await rename_agent_workdir(team_id, normalized_name, old_name)
        raise
    saved = await gtAgentManager.get_agent_by_id(agent.id) if agent.id else await gtAgentManager.get_agent(team_id, normalized_name, status=None)
    if saved is None:
        return {"success": False, "message": f"成员保存失败: {normalized_name}"}

    action = "更新" if existing is not None else "创建"
    payload = saved.to_json()
    payload["driver"] = saved.driver.value
    payload["employ_status"] = saved.employ_status.name
    # PATCH 语义下 role_template 可能没给（更新时省略 = 保持现有绑定），此时
    # `role_template` 是 None —— 直接取 `.name` 会 AttributeError。用落库后的
    # role_template_id 反查名字，保证「省略」路径的返回体与给值路径同形。
    template_name = role_template.name if role_template is not None else None
    if template_name is None and saved.role_template_id is not None:
        saved_template = await gtRoleTemplateManager.get_role_template_by_id(saved.role_template_id)
        template_name = saved_template.name if saved_template is not None else None
    payload["role_template_name"] = template_name
    return {
        "success": True,
        "message": f"已{action}成员 {normalized_name}。配置已保存，需要 reload_team 后生效。",
        "agent": payload,
    }


def _collect_descendant_ids(node: GtDept) -> set[int]:
    """递归收集节点的所有后代 dept id（不含自身）。"""
    ids: set[int] = set()
    for child in node.children:
        if child.id is not None:
            ids.add(child.id)
        ids |= _collect_descendant_ids(child)
    return ids


async def save_dept(
    name: Optional[str] = None,
    responsibility: Optional[str] = None,
    manager_name: Optional[str] = None,
    member_names: Optional[list[str]] = None,
    parent_name: str | None = None,
    i18n: dict | None = None,
    overwrite: bool = False,
    dept_id: int | None = None,
    clear_fields: Optional[list[DeptClearField]] = None,
    _context: ToolCallContext = None,
) -> dict:
    """创建或更新组织（部门）。

    ⚠️ **更新已有组织时，省略某个字段 = 保持该字段现值**（不是重置）。这是 #196 的行为
    变更，理由与 `save_agent` 同：省略与「没打算改」在调用方视角下无法区分。

    Args:
        name: 组织名称，团队内唯一标识，建议使用小写字母和下划线。新建时必须给；更新时可省略（用 dept_id 定位）。
        responsibility: 组织职责描述。新建时必须给；更新时省略表示保持现值。
        manager_name: 负责人成员名；不在 member_names 中会自动加入。新建时必须给；更新时省略表示保持现值。
        member_names: 成员名称列表，全量替换而非追加。新建时必须给；更新时省略表示保持现值（不是清空成员）。
        parent_name: 父组织名称，新建时必须指定；不能是自身或自身子组织。更新时省略表示**保持现有父组织**；要把组织变成根节点须显式 clear_fields=["parent_name"]（仅当它已是根组织时才允许，否则报错——创建/删除根组织属管理员通过组织树编辑器的操作）。
        i18n: 多语言数据，如 {"dept_name": {"zh-CN": "研发部", "en": "R&D"}, "responsibility": {"zh-CN": "负责研发"}}。更新时省略表示保持现值。
        overwrite: 更新已有组织**必须**设为 true（无论按 name 还是 dept_id 定位）；默认 false 只允许新建。这是防止误覆盖已有组织的保护开关。
        dept_id: 组织 ID；传入后按 ID 精确定位，此时 name 用于重命名。
        clear_fields: 要清除的字段名列表，当前仅 "parent_name"。clear_fields=["parent_name"] 表示把组织设为根节点（须已是根，否则报错）。传空数组 [] 视为「未提供」。与 parent_name 给值同时出现报 ambiguous_clear_field。白名单之外返回 invalid_clear_field。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    if dept_id is None and name is None:
        return {
            "success": False,
            "message": "请用 dept_id 或 name 指定组织；新建组织必须给 name。",
            "error_code": toolResult.INVALID_TOOL_ARGS,
        }

    normalized_name: str | None = None
    if name is not None:
        normalized_name = name.strip()
        if not normalized_name:
            return {"success": False, "message": "组织名称不能为空。"}

    clear_names, clear_error = _normalize_clear_fields(
        clear_fields, ALLOWED_DEPT_CLEAR_FIELDS, "save_dept"
    )
    if clear_error is not None:
        return clear_error
    clearing = set(clear_names)
    conflict = _clear_field_conflict(clearing, {"parent_name": parent_name}, "save_dept")
    if conflict is not None:
        return conflict

    from dal.db import gtDeptManager
    from service import deptService

    # 按 ID 或名称定位已有组织
    if dept_id is not None:
        existing = await gtDeptManager.get_dept_by_id(dept_id)
        if existing is None or existing.team_id != team_id:
            return {"success": False, "message": f"未找到 ID 为 {dept_id} 的组织。"}
    else:
        existing = await gtDeptManager.get_dept_by_name(team_id, normalized_name)

    # 更新保护对两种定位方式一视同仁：按 ID 也是更新已有记录，必须显式确认。
    if existing is not None and overwrite is not True:
        return {
            "success": False,
            "message": f"组织 {existing.name} 已存在；如需覆盖请将 overwrite 设为 true。",
        }

    # 新建时的必填项在运行时补齐（schema 层已放开，见上方说明）。
    if existing is None:
        missing = [
            field
            for field, given in (
                ("name", normalized_name),
                ("responsibility", responsibility),
                ("manager_name", manager_name),
                ("member_names", member_names),
            )
            if given is None
        ]
        if missing:
            return {
                "success": False,
                "message": f"新建组织时必须提供 {'、'.join(missing)}。",
                "error_code": toolResult.INVALID_TOOL_ARGS,
            }

    all_agents = await gtAgentManager.get_team_all_agents(team_id)
    name_to_agent: dict[str, Any] = {a.name: a for a in all_agents}
    id_to_name: dict[int, str] = {a.id: a.name for a in all_agents}

    # ---- PATCH：省略的字段取现值（更新分支）----
    if existing is not None and normalized_name is None:
        normalized_name = existing.name
    effective_responsibility = responsibility
    if effective_responsibility is None and existing is not None:
        effective_responsibility = existing.responsibility

    # 负责人：省略 ⇒ 保持现负责人（反查名字）
    normalized_manager: str | None = None
    if manager_name is not None:
        normalized_manager = manager_name.strip()
        if not normalized_manager:
            return {"success": False, "message": "负责人名称不能为空。"}
    elif existing is not None:
        normalized_manager = id_to_name.get(existing.manager_id)
        if normalized_manager is None:
            return {
                "success": False,
                "message": (
                    f"组织 {existing.name} 的现负责人（id={existing.manager_id}）不在当前团队名册中，"
                    "无法保持现值；请显式指定 manager_name。"
                ),
            }

    # 成员：省略 ⇒ 保持现成员集合
    if member_names is not None:
        source_member_names = member_names
    elif existing is not None:
        missing_ids = [aid for aid in (existing.agent_ids or []) if aid not in id_to_name]
        if missing_ids:
            return {
                "success": False,
                "message": (
                    f"组织 {existing.name} 的现有成员 {missing_ids} 不在当前团队名册中，"
                    "无法保持现值；请显式指定 member_names。"
                ),
            }
        source_member_names = [id_to_name[aid] for aid in (existing.agent_ids or [])]
    else:
        source_member_names = []

    manager_agent = name_to_agent.get(normalized_manager) if normalized_manager is not None else None
    if manager_agent is None:
        return {"success": False, "message": f"未找到成员: {normalized_manager}"}

    # 解析 member_names → agent_ids，遇到找不到的立即报错
    resolved_ids: list[int] = []
    for mname in source_member_names:
        mname = mname.strip()
        agent = name_to_agent.get(mname)
        if agent is None:
            return {"success": False, "message": f"未找到成员: {mname}"}
        resolved_ids.append(agent.id)

    # 负责人自动加入成员列表
    if manager_agent.id not in resolved_ids:
        resolved_ids.insert(0, manager_agent.id)

    # 解析 parent_name → parent_id，并校验循环引用
    parent_id: int | None = None
    if "parent_name" in clearing:
        # 显式清除 = 要求该组织成为根节点。只有已经是根才允许（与改造前
        # `parent_name=None` 的校验同一口径），否则报错而不是静默改结构。
        if existing is None:
            return {"success": False, "message": "新建组织必须指定父组织（parent_name），不能用 clear_fields 清除。"}
        if existing.parent_id is not None:
            return {"success": False, "message": f"组织 {existing.name} 当前不是根组织，不能将父组织设为空。"}
        parent_id = None
    elif parent_name is not None:
        normalized_parent = parent_name.strip()
        parent_dept = await gtDeptManager.get_dept_by_name(team_id, normalized_parent)
        if parent_dept is None:
            return {"success": False, "message": f"未找到父组织: {normalized_parent}"}
        if existing is not None:
            # 不能把自身设为父组织
            if parent_dept.id == existing.id:
                return {"success": False, "message": "父组织不能设置为当前组织自身。"}
            # 不能把子孙组织设为父组织（会产生环）
            dept_tree = await deptService.get_dept_tree(team_id)
            current_node = _find_dept_node(dept_tree, existing.id)
            if current_node is not None:
                descendant_ids = _collect_descendant_ids(current_node)
                if parent_dept.id in descendant_ids:
                    return {"success": False, "message": f"父组织 {normalized_parent} 是当前组织的子组织，不能形成循环引用。"}
        parent_id = parent_dept.id
    elif existing is None:
        return {"success": False, "message": "新建组织时必须指定父组织（parent_name）。如需创建根组织，请联系管理员通过组织树编辑器操作。"}
    else:
        # 省略且非清除 ⇒ 保持现有父组织（PATCH 语义；改造前这里会报错或落成根）
        parent_id = existing.parent_id

    saved = await deptService.upsert_dept(
        team_id=team_id,
        name=normalized_name,
        responsibility=effective_responsibility,
        manager_id=manager_agent.id,
        agent_ids=resolved_ids,
        parent_id=parent_id,
        dept_id=existing.id if existing is not None else None,
        i18n=i18n if i18n is not None else (existing.i18n if existing is not None else None),
    )

    action = "更新" if existing is not None else "创建"
    return {
        "success": True,
        "message": f"已{action}组织 {normalized_name}。配置已保存，需要 reload_team 后生效。",
        "dept": _serialize_dept_node(saved, id_to_name),
    }


async def save_room(
    name: Optional[str] = None,
    member_names: Optional[list[str]] = None,
    initial_topic: str | None = None,
    max_rounds: int | None = None,
    overwrite: bool = False,
    room_id: int | None = None,
    clear_fields: Optional[list[RoomClearField]] = None,
    _context: ToolCallContext = None,
) -> dict:
    """创建或更新房间；成员 2 人为单聊，3 人及以上为群聊。

    ⚠️ **更新已有房间时，省略某个字段 = 保持该字段现值**（不是重置）。这是 #196 的行为
    变更，理由与 `save_agent` 同。

    Args:
        name: 房间名称，团队内唯一标识，建议使用小写字母和下划线。新建时必须给；更新时可省略（用 room_id 定位）。
        member_names: 成员名称列表，至少 2 人，全量替换。新建时必须给；更新时省略表示保持现成员集合。
        initial_topic: 房间初始话题。新建时省略 = 空话题（单聊会自动生成话题）；更新时省略 = 保持现值。清空用 clear_fields=["initial_topic"]。
        max_rounds: 最大轮次；<=0 表示不限轮次。新建时省略 = 用系统默认；更新时省略 = 保持现值。重置为系统默认用 clear_fields=["max_rounds"]。
        overwrite: 更新已有房间**必须**设为 true（无论按 name 还是 room_id 定位）；默认 false 只允许新建。这是防止误覆盖已有房间的保护开关。
        room_id: 房间 ID；传入后按 ID 精确定位，此时 name 用于重命名。
        clear_fields: 要清除（恢复默认）的字段名列表，可选 "initial_topic"、"max_rounds"。initial_topic 清空后：群聊为空话题；单聊仍会自动生成默认话题（沿用 `upsert_room` 既有行为，不是本次引入）。max_rounds 清除 = 回到系统默认（NULL）。传空数组 [] 视为「未提供」。与给值同时出现同一字段会报 ambiguous_clear_field。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    if room_id is None and name is None:
        return {
            "success": False,
            "message": "请用 room_id 或 name 指定房间；新建房间必须给 name。",
            "error_code": toolResult.INVALID_TOOL_ARGS,
        }

    normalized_name: str | None = None
    if name is not None:
        normalized_name = name.strip()
        if not normalized_name:
            return {"success": False, "message": "房间名称不能为空。"}

    clear_names, clear_error = _normalize_clear_fields(
        clear_fields, ALLOWED_ROOM_CLEAR_FIELDS, "save_room"
    )
    if clear_error is not None:
        return clear_error
    clearing = set(clear_names)
    conflict = _clear_field_conflict(
        clearing, {"initial_topic": initial_topic, "max_rounds": max_rounds}, "save_room"
    )
    if conflict is not None:
        return conflict

    all_agents = await gtAgentManager.get_team_all_agents(team_id)
    name_to_agent: dict[str, Any] = {a.name: a for a in all_agents}
    id_to_name: dict[int, str] = {a.id: a.name for a in all_agents}

    # 按 ID 或名称定位已有房间
    if room_id is not None:
        existing = await gtRoomManager.get_room_by_id(room_id)
        if existing is None or existing.team_id != team_id:
            return {"success": False, "message": f"未找到 ID 为 {room_id} 的房间。"}
    else:
        existing = await gtRoomManager.get_room_by_team_and_name(team_id, normalized_name)

    # 更新保护对两种定位方式一视同仁：按 ID 也是更新已有记录，必须显式确认。
    if existing is not None and overwrite is not True:
        return {
            "success": False,
            "message": f"房间 {existing.name} 已存在；如需覆盖请将 overwrite 设为 true。",
        }

    # ---- PATCH：省略的字段取现值（更新分支）----
    if existing is not None and normalized_name is None:
        normalized_name = existing.name

    if member_names is not None:
        source_member_names = member_names
    elif existing is not None:
        missing_ids = [aid for aid in (existing.agent_ids or []) if aid not in id_to_name]
        if missing_ids:
            return {
                "success": False,
                "message": (
                    f"房间 {existing.name} 的现有成员 {missing_ids} 不在当前团队名册中，"
                    "无法保持现值；请显式指定 member_names。"
                ),
            }
        source_member_names = [id_to_name[aid] for aid in (existing.agent_ids or [])]
    else:
        return {
            "success": False,
            "message": "新建房间时必须提供 member_names。",
            "error_code": toolResult.INVALID_TOOL_ARGS,
        }

    resolved_ids: list[int] = []
    for mname in source_member_names:
        mname = mname.strip()
        agent = name_to_agent.get(mname)
        if agent is None:
            return {"success": False, "message": f"未找到成员: {mname}"}
        resolved_ids.append(agent.id)

    if len(resolved_ids) < 2:
        return {"success": False, "message": f"房间成员不足 2 人（当前 {len(resolved_ids)} 人）。"}

    # 按解析后的 ID 判重（名称数量拦不住 `["alice","alice"]`），与
    # `roomService._assert_room_agents_valid` 同口径直接报错，不静默去重。
    duplicate_ids: list[int] = []
    seen_ids: set[int] = set()
    for aid in resolved_ids:
        if aid in seen_ids and aid not in duplicate_ids:
            duplicate_ids.append(aid)
        seen_ids.add(aid)
    if duplicate_ids:
        duplicate_names = [id_to_name.get(aid, str(aid)) for aid in duplicate_ids]
        return {
            "success": False,
            "message": (
                f"房间成员重复: {duplicate_names}（给了 {len(resolved_ids)} 个成员，"
                f"去重后只有 {len(seen_ids)} 人）；请去掉重复成员后重试。"
            ),
            "error_code": "duplicate_agent_ids",
        }

    # initial_topic / max_rounds：省略取现值、clear 取默认值。
    # 这里显式算出最终值再交给 `upsert_room`，因为该服务函数按「实参即最终值」写入
    # （它没有「未传」这一状态），PATCH 语义必须在工具层算完。
    if "initial_topic" in clearing:
        effective_topic = ""
    elif initial_topic is not None:
        effective_topic = initial_topic
    elif existing is not None:
        effective_topic = existing.initial_topic or ""
    else:
        effective_topic = ""

    if "max_rounds" in clearing:
        effective_max_rounds: int | None = None
    elif max_rounds is not None:
        effective_max_rounds = max_rounds
    elif existing is not None:
        effective_max_rounds = existing.max_rounds
    else:
        effective_max_rounds = None

    try:
        saved = await roomService.upsert_room(
            team_id=team_id,
            name=normalized_name,
            agent_ids=resolved_ids,
            initial_topic=effective_topic,
            max_rounds=effective_max_rounds,
            room_id=existing.id if existing is not None else None,
        )
    except Exception as exc:
        return _tool_error_result(exc)

    action = "更新" if existing is not None else "创建"
    room_type_label = "单聊" if len(resolved_ids) == 2 else "群聊"
    return {
        "success": True,
        "message": f"已{action}{room_type_label}房间 {saved.name}。配置已保存，需要 reload_team 后生效。",
        "room": {
            "room_id": saved.id,
            "name": saved.name,
            "type": room_type_label,
            "members": [id_to_name.get(aid, str(aid)) for aid in (saved.agent_ids or [])],
            "max_rounds": saved.max_rounds,
            "initial_topic": saved.initial_topic,
        },
    }


async def delete_room(
    name: Optional[str] = None,
    room_id: Optional[int] = None,
    dry_run: bool = False,
    _context: ToolCallContext = None,
) -> dict:
    """删除当前团队中的指定房间。DEPT 房间不允许删除；运行中的房间不允许删除。

    Args:
        name: 要删除的房间名称；与 room_id 二选一（至少给一个），同时给出时以 room_id 为准。
        room_id: 房间 ID。
        dry_run: 是否只预演不执行。true 时同样执行全部校验（含 DEPT / 运行中保护），只返回将要删除的清单，**不改动任何数据**。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    normalized_name = None if name is None else name.strip()
    if room_id is None and not normalized_name:
        return {
            "success": False,
            "message": "请用 room_id 或 name 指定要删除的房间。",
            "error_code": toolResult.INVALID_TOOL_ARGS,
        }

    if room_id is not None:
        target = await gtRoomManager.get_room_by_id(room_id)
        if target is None or target.team_id != team_id:
            return {"success": False, "message": f"未找到房间: {_locator_label(room_id, normalized_name)}"}
    else:
        target = await gtRoomManager.get_room_by_team_and_name(team_id, normalized_name)
        if target is None:
            return {"success": False, "message": f"未找到房间: {_locator_label(None, normalized_name)}"}

    try:
        await roomService.delete_managed_room(team_id, target.id, dry_run=dry_run)
    except Exception as exc:
        return _tool_error_result(exc)

    doomed = [{"id": target.id, "name": target.name}]
    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "would_delete": doomed,
            "message": f"dry run：将删除 1 个房间 {target.name}，未执行。",
        }
    return {
        "success": True,
        "dry_run": False,
        "deleted": doomed,
        "message": f"已删除房间 {target.name}。配置已保存，需要 reload_team 后生效。",
    }


async def delete_dept(
    name: Optional[str] = None,
    dept_id: Optional[int] = None,
    recursive: bool = False,
    dry_run: bool = False,
    _context: ToolCallContext = None,
) -> dict:
    """删除当前团队中的指定组织（部门）。

    Args:
        name: 要删除的组织名称；与 dept_id 二选一（至少给一个），同时给出时以 dept_id 为准。
        dept_id: 组织 ID。
        recursive: 是否递归删除子组织。默认 false；为 true 时会一并删除所有子孙组织。
        dry_run: 是否只预演不执行。true 时同样执行全部校验（含根组织保护、有子组织未加 recursive 的阻断），只返回将要删除的清单，**不改动任何数据**。
    """
    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "当前没有可用的团队上下文。"}

    from dal.db import gtDeptManager
    from service import deptService

    normalized_name = None if name is None else name.strip()
    if dept_id is None and not normalized_name:
        return {
            "success": False,
            "message": "请用 dept_id 或 name 指定要删除的组织。",
            "error_code": toolResult.INVALID_TOOL_ARGS,
        }

    if dept_id is not None:
        target = await gtDeptManager.get_dept_by_id(dept_id)
        if target is None or target.team_id != team_id:
            return {"success": False, "message": f"未找到组织: {_locator_label(dept_id, normalized_name)}"}
    else:
        target = await gtDeptManager.get_dept_by_name(team_id, normalized_name)
        if target is None:
            return {"success": False, "message": f"未找到组织: {_locator_label(None, normalized_name)}"}

    try:
        doomed = await deptService.delete_dept(
            team_id, target.id, recursive=recursive, dry_run=dry_run
        )
    except Exception as exc:
        return _tool_error_result(exc)

    # 回显用「将要/已删除」的完整清单（含子孙）：只回一句「已删除 X」时，用户无从核对
    # 递归到底带走了哪些子组织。
    entries = [{"id": row.id, "name": row.name} for row in doomed]
    suffix = "（含子组织）" if recursive else ""
    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "would_delete": entries,
            "message": f"dry run：将删除 {len(entries)} 个组织{suffix}，未执行。",
        }
    return {
        "success": True,
        "dry_run": False,
        "deleted": entries,
        "message": f"已删除组织 {target.name}{suffix}（共 {len(entries)} 个）。配置已保存，需要 reload_team 后生效。",
    }


async def delete_role_template(role_name: str, _context: ToolCallContext = None) -> dict:
    """删除指定的角色模板。注意：仅能删除由用户创建（USER 类型）且当前未被任何成员使用的模板。

    Args:
        role_name: 要删除的角色模板名称
    """

    normalized_name = role_name.strip()
    template = await gtRoleTemplateManager.get_role_template_by_name(normalized_name)
    if template is None:
        return {"success": False, "message": f"未找到角色模板: {role_name}"}
    if template.type == RoleTemplateType.SYSTEM:
        return {"success": False, "message": f"SYSTEM 角色模板 {template.name} 不允许通过工具删除。"}

    referenced_agents = list(
        await GtAgent.select()
        .where(GtAgent.role_template_id == template.id)
        .order_by(GtAgent.team_id, GtAgent.name)
        .aio_execute()
    )
    if referenced_agents:
        agents = [{"name": agent.name, "team_id": agent.team_id} for agent in referenced_agents]
        agent_names = ", ".join(agent["name"] for agent in agents)
        return {
            "success": False,
            "message": f"角色模板 {template.name} 正在被以下 Agent 使用，无法删除: {agent_names}",
            "agents": agents,
        }

    await gtRoleTemplateManager.delete_role_template(template.id)
    return {
        "success": True,
        "message": f"已删除角色模板 {template.name}。",
        "role_template": {"id": template.id, "name": template.name},
    }


async def _resolve_effective_immediate(
    target_room,
    sender_agent_id: int,
    insert_immediately: bool,
) -> tuple[bool, str]:
    """计算实际生效的 insert_immediately 与降级说明。

    只有「私聊 + 对方是 AI + 对方驱动支持 host_managed_turn_loop」才真正加急；
    其余情况一律降级为普通消息，并返回一句说明拼进工具返回文案（不抛错）。

    返回 (effective_immediate, downgrade_note)；downgrade_note 为空串表示未降级。
    """
    if not insert_immediately:
        return False, ""

    if target_room.room_type != RoomType.PRIVATE:
        return False, "（注：群聊不支持加急注入，已按普通消息送达）"

    # 单聊里除发送者外的另一成员。两种情况必须分开说，不能混成一句：
    # - `other_id is None`：房间内没有第二个人（数据异常）。说「对方是操作者」是事实错误，
    #   Agent 会据此去催一个不存在的对象；
    # - `other_id <= 0`：另一方是 Operator，不存在「插入对方工作上下文」，但仍要告知降级 ——
    #   Agent 主动传 true 意味着它认为「必须让对方立刻看到」，静默降级会让它按「已加急」继续推进。
    other_ids = [aid for aid in target_room.get_agent_ids() if aid != sender_agent_id]
    other_id = other_ids[0] if other_ids else None
    if other_id is None:
        return False, "（注：房间内无其他成员，已按普通消息送达）"
    if other_id <= 0:
        return False, "（注：对方是操作者，不存在运行中插入，已按普通消息送达）"

    from service import agentService

    target_agent = agentService.get_agent_or_none(other_id)
    if target_agent is None or not target_agent.host_managed_turn_loop:
        return False, "（注：对方当前驱动不支持运行中加急注入，已按普通消息送达）"

    return True, ""


async def send_chat_msg(
    room_name: str,
    msg: str,
    insert_immediately: bool = False,
    _context: ToolCallContext = None,
) -> dict:
    """向房间发送消息，消息对房间内所有成员可见。

    Args:
        room_name: 目标房间名，必须是本团队已存在的房间名
        msg: 要发送的消息正文
        insert_immediately: 是否作为加急消息立即插入对方当前工作上下文，默认 false。仅在与另一名 Agent 的单聊房间（PRIVATE）、且遇到紧急叫停、重大纠偏等必须让对方在当前行动中立刻看到的场景下设为 true；群聊、与 Operator 私聊或日常沟通请保持 false。
    """
    if _context is None:
        logger.warning("发送消息失败，聊天室上下文未设置")
        return {"success": False, "message": "当前没有可用的房间上下文。"}

    logger.info(f"发送消息: sender_id={_context.agent_id}, room={room_name}, msg={msg}")

    try:
        room_config = await gtRoomManager.get_room_by_team_and_name(_context.team_id, room_name)
        target_room = roomService.get_room(room_config.id) if room_config is not None else None
    except Exception:
        try:
            team_rooms = await gtRoomManager.get_rooms_by_team(_context.team_id)
            room_config = next((room for room in team_rooms if room.name == room_name), None)
            target_room = roomService.get_room(room_config.id) if room_config else None
        except Exception:
            target_room = None

    if target_room is None:
        logger.warning(f"send_chat_msg: 目标房间不存在 room={room_name} team_id={_context.team_id}")
        return {"success": False, "message": f"目标房间不存在: {room_name} (team_id={_context.team_id})"}

    if not target_room.can_post_message(_context.agent_id):
        logger.warning(
            "send_chat_msg: 发言者不在目标房间 agents 中 sender_id=%s room=%s team_id=%s agents=%s",
            _context.agent_id,
            room_name,
            _context.team_id,
            target_room.get_agent_ids(),
        )
        return {"success": False, "message": f"你不在房间 {target_room.name} 中，发送失败。"}

    effective_immediate, downgrade_note = await _resolve_effective_immediate(
        target_room, _context.agent_id, insert_immediately
    )

    await target_room.add_message(
        _context.agent_id,
        msg,
        insert_immediately=effective_immediate,
    )

    if _context.chat_room is not None and target_room.room_id == _context.chat_room.room_id:
        return {"success": True, "message": f"消息已送达房间{downgrade_note}。如果你还有其他工具需要调用，请继续；如果本轮操作已全部完成，请调用 finish_action 结束行动。"}

    return {"success": True, "message": f"消息已送达 {target_room.name}{downgrade_note}。如果你还有其他工具需要调用，请继续；如果本轮操作已全部完成，请调用 finish_action 结束行动。"}


async def finish_action(_context: ToolCallContext = None, confirm_no_need_talk: bool = False) -> dict:
    """结束行动，把行动机会让给下一位成员；完成所有发言和工具调用后，或确认无需行动时调用。

    Args:
        confirm_no_need_talk: 本轮未在收到消息通知的房间发言、且确认无需发言时设 true；已在对应房间发言时不得设置（直接调用即可）。直接输出的文字不算房间发言（须用 send_chat_msg），用户看不到。
    """
    if _context is None:
        logger.warning("结束行动失败，上下文未设置")
        return {"success": False, "message": "当前没有激活的上下文。"}

    job_type = _context.schedule_job.job_type if _context.schedule_job is not None else ScheduleJobType.ROOM_MESSAGE

    if job_type == ScheduleJobType.TODO_TASK:
        import service.taskService as taskService

        agent_task_id = _context.schedule_job.task_data.get("agent_task_id")
        agent_task = await gtAgentTaskManager.get_task(agent_task_id) if agent_task_id else None
        if agent_task is not None:
            is_assignee = agent_task.assignee_id == _context.agent_id
            is_reviewer = taskService.is_task_reviewer(agent_task, _context.agent_id)

            if is_assignee and agent_task.status in (TaskStatus.TODO, TaskStatus.IN_PROGRESS):
                logger.warning(f"finish_action 被拒绝，协作任务仍为 {agent_task.status.value}: agent_id={_context.agent_id}, agent_task_id={agent_task_id}")
                # 取消权限单一口径见 `taskService.can_cancel`（创建人或验收人）。
                # 执行人通常两者都不是，给它「改为 CANCELLED」只会换来 permission_denied；
                # 随后 finish_action 再被拒 → 重试耗尽 → Agent FAILED。
                # 两句文案复用 `prompts.py` 的常量，**不在这里另抄一份**——多处拷贝分叉正是本次缺陷的成因。
                cancel_instruction = (
                    TODO_TASK_CANCEL_INSTRUCTION if taskService.can_cancel(agent_task, _context.agent_id)
                    else TODO_TASK_NO_CANCEL_INSTRUCTION
                )
                return {
                    "success": False,
                    "message": f"""\
finish 失败，你负责执行的任务【{agent_task.title}】状态仍为 {agent_task.status.value}，尚未处理完毕。

请先完成任务处理：
- 若已完成，请调用 `update_task` 将状态改为 REVIEWING（有验收人时）或 DONE，并在 `note` 中写明结果。
- 若需暂缓（本轮无法完成），请调用 `update_task` 将状态改为 ON_HOLD。
{cancel_instruction}
完成后再调用 finish_action。""",
                }
            elif is_reviewer and agent_task.status == TaskStatus.REVIEWING:
                logger.warning(f"finish_action 被拒绝，协作任务待验收: agent_id={_context.agent_id}, agent_task_id={agent_task_id}")
                return {
                    "success": False,
                    "message": f"""\
finish 失败，你负责验收的任务【{agent_task.title}】依然为 reviewing状态，未完成审核。

请先完成验收：
- 验收通过：请调用 `update_task` 将状态改为 DONE。
- 打回重做：请调用 `update_task` 将状态改为 IN_PROGRESS。
完成后再调用 finish_action。""",
                }
        logger.info(f"Agent 结束协作任务行动: agent_id={_context.agent_id}")
        return {"success": True, "message": "已结束了本轮行动."}

    elif job_type == ScheduleJobType.ROOM_MESSAGE:
        if _context.chat_room is None:
            logger.warning("结束行动失败，聊天室上下文未设置")
            return {"success": False, "message": "当前没有激活的房间上下文。"}

        if confirm_no_need_talk and _context.chat_room.current_turn_has_content:
            return {
                "success": False,
                "message": "finish_action 失败：你本轮已经通过 send_chat_msg 发过消息了，不需要设置 confirm_no_need_talk=true。请直接调用 finish_action（不带任何参数）结束行动。",
            }

        if not confirm_no_need_talk and not _context.chat_room.current_turn_has_content:
            room_name = _context.chat_room.name
            return {
                "success": False,
                "message": f"""\
finish 失败，你本次行动中，未在收到消息的房间【{room_name}】发言。

1. 如果你忘记发言（或者是不小心用直接输出替代了向房间发言），那么请调用 send_chat_msg 发送消息。
2. 如果你确认不需要发言，请设置 confirm_no_need_talk=true 重新调用 finish_action。""",
            }

        logger.info(f"Agent 结束行动: agent_id={_context.agent_id}")
        ok = await _context.chat_room.handle_finish_request(_context.agent_id)

        if not ok:
            current_id = _context.chat_room.get_current_turn_agent_id()
            logger.warning(f"finish_turn 被房间拒绝（发言位不匹配），但仍视为行动结束: agent_id={_context.agent_id}, current_turn_id={current_id}, room={_context.chat_room.key}")

        return {"success": True, "message": "已结束了本轮行动."}



# ---------------------------------------------------------------------------
# Task 工具：协作任务管理（委托给 taskService）
# ---------------------------------------------------------------------------

async def create_task(
    title: str,
    assignee_id: int,
    description: str = '',
    reviewer_id: Optional[int] = None,
    priority: str = 'NORMAL',
    parent_id: Optional[int] = None,
    depends_on: Optional[list[int]] = None,
    room_id: Optional[int] = None,
    status: Optional[Literal["TODO", "ON_HOLD"]] = None,
    note: str = '',
    _context: Optional[ToolCallContext] = None,
) -> dict:
    """创建协作任务（**不会唤醒执行人**，需另发 send_chat_msg 通知对方）。

    Args:
        title: 任务标题
        assignee_id: 执行人 Agent ID（只能是自己或自己的直接/间接下属）
        description: 任务描述，含上下文、约束和交付标准
        reviewer_id: 完成时若需要他人验收，则设置验收人 Agent ID，不填则无需验收流程；不能与 assignee_id 相同（同一人无需设置验收）
        priority: 优先级，HIGH / NORMAL / LOW，默认 NORMAL
        parent_id: 父任务 ID，用于任务树拆解。除了 Operator 直接下达的任务，其他任务应按拆解关系设置父任务 ID，使所有任务能形成以 Operator 任务为根的任务树
        depends_on: 依赖的任务 ID 列表。非空且其中任一尚未 DONE 时，新任务初始状态为 PENDING（依赖全部 DONE 后自动转 TODO）；为空则初始状态为 TODO
        room_id: 关联房间 ID，便于溯源
        status: 指定初始状态，可选 TODO / ON_HOLD。ON_HOLD 表示创建即搁置（优先于依赖推导）；TODO 与省略等价，由系统按 depends_on 自动判断（有未完成依赖为 PENDING，否则 TODO）
        note: 创建说明，写入创建事件；status=ON_HOLD 时作为「当前搁置原因」
    """
    import service.taskService as taskService

    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "无法获取团队上下文"}

    return await taskService.create_task(
        team_id=team_id,
        creator_id=_context.agent_id,  # type: ignore[union-attr]
        title=title,
        assignee_id=assignee_id,
        description=description,
        reviewer_id=reviewer_id,
        priority=priority,
        parent_id=parent_id,
        depends_on=[int(x) for x in (depends_on or [])],
        room_id=room_id,
        status=status,
        note=note,
    )


async def update_task(
    task_id: int,
    new_status: Optional[str] = None,
    note: str = '',
    reviewer_id: Optional[int] = None,
    clear_fields: Optional[list[ClearField]] = None,
    _context: Optional[ToolCallContext] = None,
) -> dict:
    """更新协作任务：流转状态、追加评论，或修改/撤销验收人（任何团队成员都可以追加评论）。

    Args:
        task_id: 任务 ID
        new_status: 目标状态，可选 TODO、PENDING、IN_PROGRESS、ON_HOLD、REVIEWING、DONE、CANCELLED；省略或等于当前状态表示追加评论。权限：提交验收（→REVIEWING）只能由执行人发起，且任务须设有验收人；通过或打回验收（REVIEWING→DONE、REVIEWING→IN_PROGRESS）只能由验收人操作；取消（→CANCELLED）只能由验收人或创建人操作；其余流转只能由执行人操作。终态 DONE、CANCELLED 不可流转，只能追加评论；设有验收人的任务不能绕过验收直接到 DONE；依赖任务未全部 DONE 时 PENDING 不能转为 IN_PROGRESS。
        note: 本次流转的说明；追加评论时必填，作为评论正文。修改验收人时作为变更原由写入时间线（可选）。
        reviewer_id: 把验收人设为该团队成员（写时间线一条「验收人变更：旧 → 新」）。只有任务创建人或当前验收人可以操作，执行人不能操作；不能设成执行人自己；与当前验收人相同时不写事件。省略表示不改验收人。**不能与状态流转（非自环的 new_status）在同一次调用里做**。
        clear_fields: 要清除（置空）的字段名列表，白名单当前仅 "reviewer_id"。clear_fields=["reviewer_id"] 表示撤销验收人（置空）：只有任务创建人可以操作，任务处于 REVIEWING 时不允许（会导致无人能通过验收），不能与状态流转或 reviewer_id 同时给。传空数组 [] 视为「未提供」，不会撤销任何字段（注意这与 list_tasks 的 status=[] 语义相反：status=[] 是「过滤条件写了个空的」，意图不明故报错；clear_fields=[] 是「没要清任何字段」，意图明确故等价省略）。白名单之外的字段名会被拒绝并返回 error_code="invalid_clear_field"。
    """
    import service.taskService as taskService

    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "无法获取团队上下文"}

    return await taskService.update_task(
        team_id=team_id,
        caller_id=_context.agent_id,  # type: ignore[union-attr]
        task_id=task_id,
        new_status=new_status,
        note=note,
        reviewer_id=reviewer_id,
        clear_fields=clear_fields,
    )


async def get_task_detail(
    task_id: int,
    event_limit: int = DEFAULT_EVENT_LIMIT,
    _context: Optional[ToolCallContext] = None,
) -> dict:
    """查询单个协作任务详情，包含依赖任务状态摘要与事件时间线摘要。
    默认返回最近 3 条事件；events_total 是事件总数（跨 limit），大于本页条数说明还有更早事件，可调大 event_limit 或改用 list_task_events 翻页。
    事件内容及派生的 last_note / current_block_reason 超过 100 字符会截断，完整原文用 get_task_event。

    Args:
        task_id: 任务 ID
        event_limit: 返回的最近事件条数上限，默认 3
    """
    import service.taskService as taskService

    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "无法获取团队上下文"}

    return await taskService.get_task(team_id=team_id, task_id=task_id, event_limit=event_limit)


async def get_task_event(
    event_id: int,
    _context: Optional[ToolCallContext] = None,
) -> dict:
    """读取当前团队的一条任务事件，返回完整原文 content 与 actor 展示名；用于获取 get_task_detail / list_task_events 中被截断的事件内容，本工具不受摘要截断限制。

    Args:
        event_id: 事件 ID，不是任务 ID
    """
    import service.taskService as taskService

    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "无法获取团队上下文"}

    return await taskService.get_task_event(team_id=team_id, event_id=event_id)


async def list_task_events(
    task_id: int,
    limit: int = 50,
    before_id: Optional[int] = None,
    _context: Optional[ToolCallContext] = None,
) -> dict:
    """读取任务事件时间线，最新在前。返回 events（本页，按 id 降序）、count（本页条数）、total（事件总数，跨页）；事件内容超过 100 字符会截断，完整原文用 get_task_event。

    Args:
        task_id: 任务 ID
        limit: 本页最多返回条数，默认 50
        before_id: 只返回 id 小于该值的事件；翻页时传上一页最后一条的 id，省略则取最新页
    """
    import service.taskService as taskService

    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "无法获取团队上下文"}

    return await taskService.list_task_events(
        team_id=team_id,
        task_id=task_id,
        limit=limit,
        before_id=before_id,
    )


async def list_tasks(
    assignee: Optional[int] = None,
    reviewer: Optional[int] = None,
    scope: Optional[Literal["self", "team"]] = None,
    status: Optional[
        list[Literal["TODO", "PENDING", "IN_PROGRESS", "ON_HOLD", "REVIEWING", "DONE", "CANCELLED"]]
    ] = None,
    title_keyword: Optional[str] = None,
    open_only: bool = True,
    limit: Optional[int] = None,
    _context: Optional[ToolCallContext] = None,
) -> dict:
    """查询协作任务列表。assignee / reviewer / scope / title_keyword 至少传其一；返回按最新在前，total > count 表示被截断。
    每个任务含 assignee_name / reviewer_name，无需再查 Agent 名字。

    Args:
        assignee: 按执行人 Agent ID 过滤
        reviewer: 按验收人 Agent ID 过滤
        scope: self=与我相关的任务；team=我可见范围内的任务
        title_keyword: 按任务标题搜（子串匹配）：如 "登录" 能命中标题含「登录」的任务；大小写不敏感，匹配不到时返回空列表
        status: 按状态过滤，传状态数组（如 ["TODO","DONE"]）。有效值（大写）TODO / PENDING / IN_PROGRESS / ON_HOLD / REVIEWING / DONE / CANCELLED；数组含非法值时整单报错并列出非法项（不静默丢弃）；指定时 open_only 不生效
        open_only: 只返回未完结任务（排除 DONE / CANCELLED），默认 True
        limit: 本次返回条数，默认 20，取值 1-50，超出范围按边界收敛（实际生效值在返回体 limit 字段回显）
    """
    import service.taskService as taskService

    ok, team_id = _require_team_context(_context)
    if not ok:
        return {"success": False, "message": "无法获取团队上下文"}

    # 纯空白关键字不算过滤条件：`LIKE '%%'` 会匹配全部任务，等于静默取消过滤。
    title_keyword = (title_keyword or "").strip() or None

    if assignee is None and reviewer is None and scope is None and title_keyword is None:
        return {
            "success": False,
            "message": "请通过 assignee / reviewer / scope / title_keyword 指定过滤条件（至少提供一个）",
            "error_code": "missing_target",
        }

    return await taskService.list_tasks(
        team_id=team_id,
        assignee_id=assignee,
        reviewer_id=reviewer,
        status=status,
        title_keyword=title_keyword,
        # Agent 侧要「最新在前」，与 Web 列表的优先级排序区分开（DAL 默认值不变）。
        order=gtAgentTaskManager.ORDER_ID_DESC,
        open_only=open_only,
        limit=_clamp_list_tasks_limit(limit),
        scope=scope,
        caller_id=_context.agent_id if _context is not None else None,
    )


async def load_skill(
    skill_name: str,
    _context: Optional[ToolCallContext] = None,
) -> dict:
    """加载指定技能的完整信息，包含详细操作指引和文件清单。

    Args:
        skill_name: 要加载的技能名称。仅能加载当前 Agent 已授权的技能，未授权的技能会被拒绝。
    """
    if not _context or not _context.agent_id:
        return {"success": False, "message": "无法获取当前 Agent 上下文"}

    agent = await gtAgentManager.get_agent_by_id(_context.agent_id)
    if agent is None:
        return {"success": False, "message": f"未找到 Agent（ID: {_context.agent_id}）"}

    allow_skills = agent.allow_skills

    # None 表示自动（允许所有技能），空列表表示不允许任何技能
    if allow_skills is not None and skill_name not in allow_skills:
        return {"success": False, "message": f"技能 {skill_name} 未对当前 Agent 开放"}

    skill_info = skillService.get_skill(skill_name)
    if skill_info is None:
        return {"success": False, "message": f"技能 {skill_name} 不存在"}

    content = skill_info.content
    if not content:
        return {"success": False, "message": f"读取技能 {skill_name} 内容失败"}

    files = skill_info.files

    return {
        "success": True,
        "skill_name": skill_info.name,
        "skill_dir": skill_info.skill_dir,
        "is_builtin": skill_info.is_builtin,
        "description": skill_info.description,
        "content": content,
        "files": files,
    }


# ---------------------------------------------------------------------------
# 容器沙箱工具（仅 TspAgentDriver 暴露，§6）
# ---------------------------------------------------------------------------


def _tool_agent_id(_context: ToolCallContext | None) -> int:
    """取容器工具调用上下文里的 Agent ID；没有上下文时拒绝调用。"""
    if _context is None or _context.agent_id <= 0:
        raise TogoException(
            "容器工具需要在 Agent 上下文中调用",
            containerService.ERROR_CONTAINER_INVALID_PARAMS,
        )
    return _context.agent_id


def _container_agent(_context: ToolCallContext | None):
    """取当前 Agent。

    容器工具只对 TSP driver 开放（§6.1 生效范围）：Native / Claude SDK 的工具
    不经过 gtsp，切换「执行环境」对它们没有意义，暴露了只会误导模型。
    执行环境切换由 Agent 转交 Driver，工具层不碰 Driver。

    动态导入（不在模块顶层 import agentService）：`tools.py` 被
    `funcToolService` 导入，而 `agentService` 又导入 `funcToolService`，
    顶层互导会成环。
    """
    from service import agentService

    agent_id = _tool_agent_id(_context)
    agent = agentService.get_agent_or_none(agent_id)
    if agent is None:
        raise TogoException(
            "当前 Agent 未加载，无法使用容器工具",
            containerService.ERROR_CONTAINER_INVALID_PARAMS,
        )
    return agent


async def create_container(
    image: Optional[str] = None,
    note: Optional[str] = None,
    _context: ToolCallContext = None,
) -> dict[str, Any]:
    """创建一个属于你的沙箱容器，用于在隔离环境中执行命令。

    创建后容器处于「已创建」状态，仍未投入使用；需要再调用 enter_container 才会把
    执行环境切换进去。容器内挂载两处目录：你的个人工作目录（可读写，对应
    /workspace/self）与团队共享目录（只读，对应 /workspace/team_shared）。删除容器
    不会删除这两处目录里的文件。

    每个 Agent 最多同时保留 3 个自己创建的容器（含已停止的），达到上限时请先删除。

    Args:
        image: 容器镜像名称，例如 "ubuntu:24.04"、"python:3.11"。必须是本地已存在的
            Linux 镜像，系统不会自动拉取；省略时使用默认镜像。
        note: 这个容器的用途说明，例如「跑前端构建」「临时验证 python 脚本」。
            可以用 list_containers 看到；创建后如需修改，由人在管理控制台用
            `container note` 命令调整。最多 200 个字符，超长会直接报错而不是截断；
            省略表示不写说明。
    """
    try:
        _container_agent(_context)
        assert _context is not None
        record = await containerService.create_container(
            _context.agent_id, image=image, note=note
        )
        container_id = record.container_id
        assert container_id is not None
        note_hint = f"，说明「{record.note}」" if record.note else ""
        return {
            "success": True,
            "container_id": container_id,
            "container_name": record.container_name,
            "image": record.image,
            "note": record.note,
            "state": "created",
            "mounts": {
                CONTAINER_SELF_MOUNT_PATH: "读写",
                CONTAINER_SHARED_MOUNT_PATH: "只读",
            },
            "message": (
                f"容器已创建（{container_id}，镜像 {record.image}{note_hint}），当前仍处于宿主环境。"
                "如需在容器内执行操作，请调用 enter_container 切换进去。"
            ),
        }
    except TogoException as exc:
        return _tool_error_result(exc)
    except Exception as exc:
        logger.warning("create_container 失败: %s", exc, exc_info=True)
        return {"success": False, "message": f"创建容器失败: {exc}", "error_code": containerService.ERROR_CONTAINER_CREATE_FAILED}


async def enter_container(container_id: str, _context: ToolCallContext = None) -> dict[str, Any]:
    """把执行环境切换到指定的容器，之后所有命令都在容器内执行。

    容器可以是你自己用 create_container 创建的（会自动启动并进入），也可以是宿主机上
    已经在运行的其他容器（会借用进来执行，不影响它的原有进程）。如果容器当前不是运行中
    状态且不是你创建的，需要先自行启动它。

    退出容器请调用 exit_to_host；退出后你自己创建的容器会自动停止（已创建状态），
    它里面的文件会保留。

    Args:
        container_id: 目标容器的 12 位容器 ID（十六进制）。不接受容器名称或镜像名。
    """
    try:
        agent = _container_agent(_context)
        target, previous_state = await agent.enter_container(container_id)
        return {
            "success": True,
            "exec_env": ExecEnv.CONTAINER.value,
            "container_id": target.container_id,
            # 外部容器取 inspect 的 `Name`（可能拿不到），自建容器取 DB 记录名。
            # 拿不到就给 null 而不是空串：空串在 JSON 里像一个合法容器名。
            "container_name": target.container_name,
            "mounted": target.is_managed,
            "workdir": CONTAINER_SELF_MOUNT_PATH if target.is_managed else "",
            "message": (
                f"已进入容器 {target.container_id}（进入前状态：{previous_state.value}），"
                f"后续命令都在容器内执行。"
                + (
                    f"你的个人工作目录在容器内挂载为 {CONTAINER_SELF_MOUNT_PATH}（可读写），"
                    f"团队共享目录为 {CONTAINER_SHARED_MOUNT_PATH}（只读）。"
                    "该容器由你创建，退出后会自动停止但保留文件。"
                    if target.is_managed
                    else "该容器不是由你创建的，退出后它仍会继续运行。"
                )
            ),
        }
    except TogoException as exc:
        return _tool_error_result(exc)
    except Exception as exc:
        logger.warning("enter_container 失败: %s", exc, exc_info=True)
        return {"success": False, "message": f"进入容器失败: {exc}", "error_code": containerService.ERROR_CONTAINER_ENTER_FAILED}


async def exit_to_host(_context: ToolCallContext = None) -> dict[str, Any]:
    """退出当前容器，回到宿主机环境继续执行。

    退出后容器里的路径（如 /workspace/self）不再可用，宿主机的工作目录恢复生效。
    你自己创建的容器会自动停止，里面的文件不会丢失。
    """
    try:
        agent = _container_agent(_context)
        await agent.exit_to_host()
        return {
            "success": True,
            "exec_env": ExecEnv.HOST.value,
            "workdir": agent.agent_workdir,
            "message": (
                "已退回到宿主机环境，宿主机工作目录恢复生效。"
                "容器内的路径（如 /workspace/self）已不可用；"
                "你自己创建的容器已自动停止，文件仍保留在容器与挂载目录中。"
            ),
        }
    except TogoException as exc:
        return _tool_error_result(exc)
    except Exception as exc:
        logger.warning("exit_to_host 失败: %s", exc, exc_info=True)
        return {"success": False, "message": f"退出容器失败: {exc}", "error_code": containerService.ERROR_CONTAINER_ENTER_FAILED}


async def list_containers(_context: ToolCallContext = None) -> dict[str, Any]:
    """查看你自己创建的容器列表、它们的实时状态，以及当前所处的执行环境。

    列表包含创建中与已停止的容器，不含宿主机上其他人或其他程序创建的容器。
    """
    try:
        agent = _container_agent(_context)
        assert _context is not None
        state = agent.get_exec_env_state()
        docker_available = await containerService.is_docker_available()
        infos = await containerService.list_agent_containers(
            _context.agent_id,
            current_container_id=state.current_container_id,
            docker_available=docker_available,
        )
        return {
            "success": True,
            "docker_available": docker_available,
            # 显式白名单：state.container_name 只供活动记录展示，不下发给模型
            "exec_env": state.exec_env.value,
            "current_container_id": state.current_container_id,
            # 走 `to_tool_dict()` 而不是 `model_dump()`：§6.2 契约字段名是 `container_id`，
            # 且不该暴露内部字段 `agent_id`。这是全仓唯一的映射点。
            "containers": [info.to_tool_dict() for info in infos],
        }
    except TogoException as exc:
        return _tool_error_result(exc)
    except Exception as exc:
        logger.warning("list_containers 失败: %s", exc, exc_info=True)
        return {"success": False, "message": f"查询容器列表失败: {exc}", "error_code": containerService.ERROR_CONTAINER_ENTER_FAILED}


async def update_container_note(
    container_id: Optional[str] = None,
    note: Optional[str] = None,
    record_id: Optional[int] = None,
    _context: ToolCallContext = None,
) -> dict[str, Any]:
    """修改一个你自己创建的容器的说明（note）。仅控制台可用，模型无法直接调用。

    参数声明顺序是契约的一部分：`container note <id> <text>` 的两个位置参数按
    schema 顺序映射（`container_id` → `note`），`record_id` 因此必须排在最后。

    Args:
        container_id: 容器的 12 位容器 ID（十六进制）。
        note: 新的容器说明，例如「跑前端构建」。最多 200 个字符，超长会直接报错而不是
            截断；空字符串表示清空说明。
        record_id: 容器列表里的记录编号（整数），与 container_id 二选一。
    """
    try:
        _container_agent(_context)
        assert _context is not None
        # 两个标识原样下传：都给了由 service 按 `delete_container` 那套「必须二选一」报错。
        # 不在工具层挑一个用 —— 静默择优会让调用方以为自己指定的那个生效了。
        record = await containerService.update_container_note(
            _context.agent_id,
            container_id=container_id,
            record_id=record_id,
            note=note,
        )
        return {
            "success": True,
            "record_id": record.id,
            "container_id": record.container_id,
            "note": record.note,
            "message": (
                f"容器说明已更新（记录编号 {record.id}"
                + (f"，容器 {record.container_id}" if record.container_id else "")
                + ("）：已清空。" if record.note is None else f"）：「{record.note}」。")
            ),
        }
    except TogoException as exc:
        return _tool_error_result(exc)
    except Exception as exc:
        logger.warning("update_container_note 失败: %s", exc, exc_info=True)
        return {
            "success": False,
            "message": f"更新容器说明失败: {exc}",
            "error_code": containerService.ERROR_CONTAINER_INVALID_PARAMS,
        }


async def delete_container(
    container_id: Optional[str] = None,
    record_id: Optional[int] = None,
    _context: ToolCallContext = None,
) -> dict[str, Any]:
    """删除一个你自己创建的容器。

    两种指定方式二选一：用容器的 12 位 ID，或用列表里的记录编号（创建中、还没有 ID
    的容器只能用记录编号）。正在使用的容器不能删除，需要先调用 exit_to_host 退出。

    删除会移除容器本身，但不会删除你的个人工作目录与团队共享目录里的文件。

    Args:
        container_id: 容器的 12 位容器 ID（十六进制）。
        record_id: 容器列表里的记录编号（整数），与 container_id 二选一。
    """
    try:
        agent = _container_agent(_context)
        assert _context is not None
        state = agent.get_exec_env_state()
        record = await containerService.delete_container(
            _context.agent_id,
            container_id=container_id,
            record_id=record_id,
            current_container_id=state.current_container_id,
        )
        return {
            "success": True,
            "deleted_record_id": record.id,
            "deleted_container_id": record.container_id,
            "message": (
                f"容器已删除（记录编号 {record.id}"
                + (f"，容器 {record.container_id}" if record.container_id else "")
                + "）。该容器挂载的目录里的文件不受影响。"
            ),
        }
    except TogoException as exc:
        return _tool_error_result(exc)
    except Exception as exc:
        logger.warning("delete_container 失败: %s", exc, exc_info=True)
        return {"success": False, "message": f"删除容器失败: {exc}", "error_code": containerService.ERROR_CONTAINER_DELETE_FAILED}


# ---------------------------------------------------------------------------
# 长期记忆工具（memory_*）
#
# 身份注入：5 个工具都通过 `_context: ToolCallContext` 拿 `agent_id` / `team_id`，
# **不向 LLM 暴露 team_id / agent_id**——让模型传自己的 ID 只是多一个可以传错、
# 或被诱导去传别人的参数。归属校验一律在 `memoryService` 里按上下文 ID 做。
# ---------------------------------------------------------------------------


def _require_memory_context(_context: ToolCallContext | None) -> tuple[int, int]:
    """取记忆工具需要的 `(team_id, agent_id)`；缺上下文直接拒绝调用。"""
    if _context is None or _context.agent_id <= 0 or _context.team_id <= 0:
        raise TogoException(
            "记忆工具需要在 Agent 上下文中调用（缺少有效的 agent_id / team_id）",
        )
    return _context.team_id, _context.agent_id


async def memory_list(
    include_archived: bool = False,
    _context: ToolCallContext = None,
) -> dict[str, Any]:
    """列出你自己的长期记忆目录（L1：ID、标题、星标级别、标签、更新时间），不含正文。

    默认只列活跃记忆。需要按 ID 恢复/反悔已归档的记忆时，把 include_archived 设为 true
    才会拿到归档记忆的 ID（检索只覆盖活跃记忆）。

    Args:
        include_archived: 是否一并返回已归档记忆，默认 false（只看活跃记忆）
    """
    try:
        team_id, agent_id = _require_memory_context(_context)
        memories = await memoryService.list_memories(
            team_id, agent_id, include_archived=include_archived,
        )
        items = [memory.model_dump(mode="json") for memory in memories]
        return {"success": True, "count": len(items), "memories": items}
    except TogoException as exc:
        return _tool_error_result(exc)


async def memory_read(
    memory_id: int,
    _context: ToolCallContext = None,
) -> dict[str, Any]:
    """读取一条记忆的完整正文（L3）。只在确实需要具体执行依据、完整步骤时才用。

    支持读取已归档记忆（例如刚归档就想反悔恢复）。ID 不存在或不属于你时统一报不存在。

    Args:
        memory_id: 记忆 ID（来自 memory_list / memory_search / 提示词里的 #id）
    """
    try:
        team_id, agent_id = _require_memory_context(_context)
        detail = await memoryService.read_memory(team_id, agent_id, memory_id)
        return {"success": True, "memory": detail.model_dump(mode="json")}
    except TogoException as exc:
        return _tool_error_result(exc)


async def memory_search(
    query: str,
    limit: int = 5,
    _context: ToolCallContext = None,
) -> dict[str, Any]:
    """用关键词检索你的长期记忆，返回概要 + 命中正文片段（L2），不含完整正文。

    多个关键词用空格分隔，会按字段权重加权打分（标题 > 标签 > 概要 > 正文）后排序。
    只在活跃记忆里检索。查不到时返回空列表（不会报错）。

    Args:
        query: 检索关键词，多个关键词用空格分隔；不能为空
        limit: 返回条数上限，默认 5，取值 1-20（超出按边界收敛）
    """
    try:
        team_id, agent_id = _require_memory_context(_context)
        results = await memoryService.search_memories(
            team_id, agent_id, query=query, limit=limit,
        )
        items = [item.model_dump(mode="json") for item in results]
        return {"success": True, "count": len(items), "memories": items}
    except TogoException as exc:
        return _tool_error_result(exc)


async def memory_create(
    title: str,
    summary: str,
    content: str,
    star_level: str = "none",
    tags: Optional[list[str]] = None,
    _context: ToolCallContext = None,
) -> dict[str, Any]:
    """新建一条长期记忆槽位（一个槽位一个独立主题）。

    只在信息具有跨任务长期价值时使用（用户偏好、已确认的技术决策、核心边界约束）。
    不要把临时闲聊、敏感信息（密码 / 私钥 / Token）、未证实的推测记进来。
    同一主题已有记忆时请改用 memory_update，不要重复建槽（同名活跃标题会被拒绝）。

    Args:
        title: 唯一主题标题，单行，≤ 80 字符
        summary: 单句结论概要，单行，≤ 100 字符
        content: 完整可执行正文，≤ 1000 字符（可含换行）
        star_level: 星标级别，none（默认，平时不进提示词）/ title（始终知道主题存在）/ summary（ID、标题与结论始终可见）
        tags: 标签数组，最多 5 个，单个 ≤ 24 字符
    """
    try:
        team_id, agent_id = _require_memory_context(_context)
        detail = await memoryService.create_memory(
            team_id,
            agent_id,
            title=title,
            summary=summary,
            content=content,
            star_level=star_level,
            tags=tags,
        )
        return {"success": True, "id": detail.id, "title": detail.title}
    except TogoException as exc:
        return _tool_error_result(exc)


async def memory_update(
    memory_id: int,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    content: Optional[str] = None,
    star_level: Optional[str] = None,
    status: Optional[str] = None,
    tags: Optional[list[str]] = None,
    _context: ToolCallContext = None,
) -> dict[str, Any]:
    """更新或归档一条已有记忆。未传的字段保持不变（传了就按该字段的规则校验）。

    修改正文（content）时必须同时传入最新的 summary，保证概要与正文一致；
    归档（status="archived"）会自动把星标重置为 none、释放星标额度。

    Args:
        memory_id: 记忆 ID
        title: 新标题，单行，≤ 80 字符
        summary: 新概要，单行，≤ 100 字符（改 content 时必传）
        content: 新正文，≤ 1000 字符（可含换行）
        star_level: 新星标级别：none / title / summary（活跃星标总数上限 12，其中 summary 上限 6）
        status: 新状态：active / archived（归档自动剥离星标）
        tags: 新标签数组，最多 5 个，单个 ≤ 24 字符
    """
    try:
        team_id, agent_id = _require_memory_context(_context)
        detail = await memoryService.update_memory(
            team_id,
            agent_id,
            memory_id,
            title=title,
            summary=summary,
            content=content,
            star_level=star_level,
            status=status,
            tags=tags,
        )
        return {"success": True, "id": detail.id, "title": detail.title}
    except TogoException as exc:
        return _tool_error_result(exc)
