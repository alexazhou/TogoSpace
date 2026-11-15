"""MCP 注册表 console/工具层判据（#224，按 #223 设计稿）。

四类判据：
1. **命令表形态**（不碰库）：add/rm/ls 三条 spec 的转发形态与 arity、三通道等价、
   `--kind` 重复报 `duplicate_option`（#218 联名）、`--target` 与位置参数冲突报
   `ambiguous_input_source`（§3.6-5）；
2. **真入口闭环**（真库）：`mcp add → mcp ls → mcp call → mcp rm`，call 步经
   `execute_tool_call` 第 2 跳真实执行；mcp2cli 缺席的环境用 **stub `_main_impl`**
   （捕获 runtime argv），真包路径另由
   `tests/integration/test_funcToolService/test_mcp_registry_real_loop.py`
   （`importorskip`）覆盖；
3. **name 解析语义**（§3.6 / 1337 附加判据 3）：`resolved_from_registry`/`name` 恒出现、
   kind 冲突三分支、`mcp ls <name>` 与 `mcp call` 复用同一 `resolve_name`；
4. **权限与凭证**（T6 / 1331 → #277）：console 面与直调面普通成员即可写入（#277 取消
   admin_denied）；日志/返回体/异常文案 grep 不到明文值（出口打码，`_mask_deep`）。
"""

from __future__ import annotations

import json
import logging
import sys
import types
from pathlib import Path

import pytest

from container_db_schema import migrate_all
from exception import ForwardToolCall
from model.dbModel.base import bind_database, unbind_database
from service import mcpRegistryService
from service.agentService import toolRegistry
from service.funcToolService import core as func_tool_core
from service.funcToolService import mcpTool
from service.funcToolService.consoleTool import console
from service.funcToolService.tools import (
    delete_mcp_server,
    list_mcp_servers,
    mcp_call,
    save_mcp_server,
)
from service.ormService import AioSqliteDatabase
from util import llmApiUtil

_SECRET = "sk-super-secret-value-should-never-log"
_VAR = "GITHUB_TOKEN"
#: F6 靶值：含 `'` 与 `\` —— repr 转义后明文串不再字面出现，「只搜字面量」会假绿。
_TRICKY = "sk-a'b\\cd-1234567890"


def _repr_form(value: str) -> str:
    """值在 repr 输出里的形态（去外层引号）：' → \\'、\\ → \\\\。"""
    return repr(value)[1:-1]


def _unescape(text: str) -> str:
    """日志文本的去转义形态（判据第三形态）。

    逐轮去转义至稳定（最多 4 轮）：「先 repr 再打码」型缺陷会把明文转义后还可能
    被 `%r` **再转义一层**（MUT-11 实测：单轮去转义搜不到 ⇒ 假绿）。`\\\\` 先收敛、
    再收 `\\'`，两规则反复套用直到文本不再变化。
    """
    for _ in range(4):
        nxt = text.replace("\\\\", "\\").replace("\\'", "'")
        if nxt == text:
            break
        text = nxt
    return text


def _secret_forms(value: str) -> tuple[str, str]:
    """反向判据必搜的形态：原始值 + repr 转义形态（任一命中即失败）。"""
    return (value, _repr_form(value))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def mcp_db(tmp_path: Path):
    db_path = tmp_path / "mcp_registry_console.db"
    applied = migrate_all(db_path, workspace_root=tmp_path)
    assert "0023_add_mcp_servers.sql" in applied
    db = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()
    try:
        yield db_path
    finally:
        await db.aio_close()
        unbind_database()


@pytest.fixture
def leader_ctx():
    return toolRegistry.ToolCallContext(agent_id=1, team_id=1, chat_room=None)


@pytest.fixture
def loaded_tools():
    func_tool_core.load_func_tools()
    func_tool_core.load_container_func_tools()


def _build_registry() -> toolRegistry.AgentToolRegistry:
    registry = toolRegistry.AgentToolRegistry()
    for tool in func_tool_core.get_all_tools():
        registry.register(tool, func_tool_core.run_tool_call)
    return registry


class _StubMcp2Cli(types.ModuleType):
    """假 mcp2cli：记录 runtime argv（解析后的），回一份合法 JSON。

    只替外部包的执行分派这一跳；解析→argv 构建→日志→解析→分派→返回体整条
    ToGo 链路都是真的。
    """

    runtime_argvs: list[list[str]] = []

    @staticmethod
    def to_kebab(key: str) -> str:
        return key.replace("_", "-")

    @staticmethod
    def _main_impl(argv: list[str]) -> None:
        _StubMcp2Cli.runtime_argvs.append(list(argv))
        import sys as _sys

        _sys.stdout.write(
            json.dumps({"stub": True, "argv": list(argv)}, ensure_ascii=False)
        )


@pytest.fixture
def stub_mcp2cli(monkeypatch):
    stub = _StubMcp2Cli("mcp2cli")
    _StubMcp2Cli.runtime_argvs = []
    monkeypatch.setitem(sys.modules, "mcp2cli", stub)
    return stub


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv(_VAR, _SECRET)
    return _SECRET


async def _register_server(
    name: str = "ctx7",
    kind: str = "mcp",
    target: str = "https://mcp.context7.com/mcp",
    credentials: dict | None = None,
    team_id: int = 1,
) -> dict:
    return await save_mcp_server(
        name=name,
        kind=kind,
        target=target,
        credentials=credentials,
        _context=toolRegistry.ToolCallContext(agent_id=1, team_id=team_id),
    )


# ---------------------------------------------------------------------------
# 1. 命令表形态（不碰库）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_add_inline_json_forwards_to_save():
    payload = {"name": "ctx7", "kind": "mcp", "target": "https://mcp.context7.com/mcp"}
    with pytest.raises(ForwardToolCall) as excinfo:
        await console(f"mcp add '{json.dumps(payload)}'")
    forward = excinfo.value
    assert forward.tool_name == "save_mcp_server"
    assert forward.tool_args == payload


@pytest.mark.asyncio
async def test_mcp_add_option_form_forwards_same_shape():
    with pytest.raises(ForwardToolCall) as excinfo:
        await console("mcp add --name ctx7 --kind mcp --target https://mcp.context7.com/mcp")
    forward = excinfo.value
    assert forward.tool_args == {
        "name": "ctx7",
        "kind": "mcp",
        "target": "https://mcp.context7.com/mcp",
    }


@pytest.mark.asyncio
async def test_mcp_add_from_payload_channel_equivalent():
    payload = {"name": "ctx7", "target": "https://x", "note": "n"}
    with pytest.raises(ForwardToolCall) as excinfo:
        await console("mcp add --from payload", payload=payload)
    assert excinfo.value.tool_args == payload


async def _console_error(command: str, payload: dict | None = None) -> dict:
    """console() 对 ConsoleError 的对外契约是失败返回体（不抛），这里取 error_code。"""
    body = await console(command, payload=payload)
    assert body.get("success") is False, body
    return body


@pytest.mark.asyncio
async def test_mcp_add_rejects_two_positional_args():
    """accepts_input 分支写死的 upper=1 才是真正的拦截（max_args=0 只是同构写法）。"""
    body = await _console_error("mcp add ctx7 https://x")
    assert body["error_code"] == "invalid_argument_count"


@pytest.mark.asyncio
async def test_mcp_add_nothing_reports_missing_input_source():
    body = await _console_error("mcp add")
    assert body["error_code"] == "missing_input_source"


@pytest.mark.asyncio
async def test_mcp_add_duplicate_kind_reports_duplicate_option():
    """联名 #218：标量选项重复出现报 duplicate_option，不静默取最后一个。"""
    body = await _console_error(
        "mcp add --name ctx7 --kind mcp --kind openapi --target https://x"
    )
    assert body["error_code"] == "duplicate_option"


@pytest.mark.asyncio
async def test_mcp_rm_forwards_name_and_arity():
    with pytest.raises(ForwardToolCall) as hit:
        await console("mcp rm ctx7")
    assert hit.value.tool_name == "delete_mcp_server"
    assert hit.value.tool_args == {"name": "ctx7"}

    body = await _console_error("mcp rm a b")
    assert body["error_code"] == "invalid_argument_count"

    body = await _console_error("mcp rm")
    assert body["error_code"] == "invalid_argument_count"


@pytest.mark.asyncio
async def test_mcp_ls_no_arg_and_one_arg_both_reach_the_tool():
    """§3.3：0 段与 1 段位置参数都要到达工具——这是换 DIRECT 的唯一原因。"""
    with pytest.raises(ForwardToolCall) as empty:
        await console("mcp ls")
    assert empty.value.tool_name == "list_mcp_servers"
    assert empty.value.tool_args == {}

    with pytest.raises(ForwardToolCall) as one:
        await console("mcp ls ./openapi.json")
    assert one.value.tool_args == {"name_or_target": "./openapi.json"}


@pytest.mark.asyncio
async def test_mcp_call_positional_and_target_conflict():
    """§3.6-5：位置参数与 --target 同时给 ⇒ ambiguous_input_source，不新增「name优先」。"""
    body = await _console_error("mcp call ctx7 --target https://x")
    assert body["error_code"] == "ambiguous_input_source"


@pytest.mark.asyncio
async def test_help_overlay_mentions_registry_semantics():
    """T5 硬性动作：help 必须写「登记到 team 级注册表，非创建业务实体」。"""
    from service.funcToolService import consoleTool

    overlay = "\n".join(consoleTool._HELP_OVERLAY.get("mcp add", ()))
    assert "非创建业务实体" in overlay
    assert "kind 默认 mcp" in overlay
    ls_overlay = "\n".join(consoleTool._HELP_OVERLAY.get("mcp ls", ()))
    assert "无参" in ls_overlay


# ---------------------------------------------------------------------------
# 2. 真入口闭环（真库 + stub 外部包）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closed_loop_add_ls_call_rm(mcp_db, leader_ctx, loaded_tools, stub_mcp2cli):
    """真入口闭环：add（真写库）→ ls（真读库）→ call（第 2 跳真执行 + name 解析）
    → rm（真删库）。每步都是真链路；唯一替身是外部包的分派函数。"""
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute", "Category:Admin"]
    )

    def _call(name: str, command: str) -> llmApiUtil.OpenAIToolCall:
        return llmApiUtil.OpenAIToolCall(
            id=f"tc_{name}",
            function={"name": "console", "arguments": json.dumps({"command": command})},
        )

    # add（真写库）
    added = await registry.execute_tool_call(
        _call("add", "mcp add --name ctx7 --kind mcp --target https://mcp.context7.com/mcp"),
        context=leader_ctx,
    )
    body = added.result
    assert body.get("success") is True, body
    assert body["server"]["name"] == "ctx7"

    # ls 无参（真读库，脱敏）
    listed = await registry.execute_tool_call(_call("ls", "mcp ls"), context=leader_ctx)
    body = listed.result
    assert body["success"] is True
    assert body["count"] == 1
    assert body["servers"][0]["name"] == "ctx7"
    assert body["servers"][0]["target_masked"] == "https://mcp.context7.com/mcp"
    assert "result" not in body  # 无参分支不套 mcp_call 的返回结构（设计稿 §3.3）

    # call（name 解析 + 第 2 跳真执行）
    called = await registry.execute_tool_call(
        _call("call", "mcp call ctx7 --tool list_issues"), context=leader_ctx
    )
    body = called.result
    assert body.get("success") is True, body
    assert body["resolved_from_registry"] is True
    assert body["name"] == "ctx7"
    assert stub_mcp2cli.runtime_argvs, "call 必须真的分派一次"
    argv = stub_mcp2cli.runtime_argvs[-1]
    assert argv[0:3] == ["--mcp", "https://mcp.context7.com/mcp", "--json"], argv
    assert "list_issues" in argv

    # rm（真删库）
    removed = await registry.execute_tool_call(_call("rm", "mcp rm ctx7"), context=leader_ctx)
    body = removed.result
    assert body["success"] is True
    assert body["deleted"]["name"] == "ctx7"

    after = await registry.execute_tool_call(_call("ls2", "mcp ls"), context=leader_ctx)
    assert after.result["count"] == 0


@pytest.mark.asyncio
async def test_rm_missing_name_reports_not_found(mcp_db, leader_ctx, loaded_tools):
    result = await delete_mcp_server(name="ghost", _context=leader_ctx)
    assert result["success"] is False
    assert result["error_code"] == "mcp_name_not_found"


# ---------------------------------------------------------------------------
# 3. name 解析语义（§3.6 / 1337 附加判据 3）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolved_from_registry_always_present(
    mcp_db, leader_ctx, loaded_tools, stub_mcp2cli
):
    await _register_server()

    hit = await list_mcp_servers(name_or_target="ctx7", _context=leader_ctx)
    assert hit["resolved_from_registry"] is True
    assert hit["name"] == "ctx7"

    miss = await list_mcp_servers(name_or_target="https://direct.example.com", _context=leader_ctx)
    assert miss["resolved_from_registry"] is False
    assert "name" not in miss

    # 未命中的 target 原样透传（向后兼容）：argv 用的是传入值本身。
    assert stub_mcp2cli.runtime_argvs[-1][1] == "https://direct.example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("given_kind", "expect_ok", "expect_error"),
    [
        (None, True, None),          # 缺省 ⇒ 放行，kind 取注册值（mcp）
        ("mcp", True, None),         # 相同 ⇒ 放行（第三支的反向覆盖）
        ("graphql", False, "mcp_name_kind_conflict"),
        ("openapi", True, None),     # 签名默认值与缺省不可区分，视为缺省（§3.6 已承认）
    ],
)
async def test_kind_conflict_three_branches(
    mcp_db, leader_ctx, loaded_tools, stub_mcp2cli, given_kind, expect_ok, expect_error
):
    """kind 三分支（1331 批准口径）：命中取注册值；不同 ⇒ conflict；相同/缺省 ⇒ 放行。"""
    await _register_server(kind="mcp")
    kwargs = {}
    if given_kind is not None:
        kwargs["kind"] = given_kind
    from service.funcToolService.tools import mcp_call

    body = await mcp_call(target="ctx7", action="list", tool=None, **kwargs, _context=leader_ctx)
    if expect_ok:
        assert body["success"] is True, body
        assert body["resolved_from_registry"] is True
        assert stub_mcp2cli.runtime_argvs[-1][0] == "--mcp"  # kind 取了注册值
    else:
        assert body["success"] is False
        assert body["error_code"] == expect_error
        assert stub_mcp2cli.runtime_argvs == [], "冲突时不得发起调用"


@pytest.mark.asyncio
async def test_ls_and_call_share_one_resolve_name(mcp_db, leader_ctx, loaded_tools, stub_mcp2cli):
    """C3：`mcp ls <name>` 与 `mcp call` 必须复用同一条 resolve_name（不许第二套）。"""
    await _register_server(kind="graphql", target="https://gql.example.com")

    listed = await list_mcp_servers(name_or_target="ctx7", kind="mcp", _context=leader_ctx)
    assert listed["success"] is False
    assert listed["error_code"] == "mcp_name_kind_conflict", listed

    called = await list_mcp_servers(name_or_target="ctx7", _context=leader_ctx)
    assert called["success"] is True
    assert stub_mcp2cli.runtime_argvs[-1][0] == "--graphql"


@pytest.mark.asyncio
async def test_call_without_team_context_skips_resolution(
    loaded_tools, stub_mcp2cli, mcp_db
):
    """无 team 上下文时按未命中透传（既有调用方行为不变）。"""
    body = await list_mcp_servers(name_or_target="ctx7", _context=None)
    assert body["resolved_from_registry"] is False
    assert stub_mcp2cli.runtime_argvs[-1][1] == "ctx7"


@pytest.mark.asyncio
async def test_call_without_bound_db_skips_resolution(loaded_tools, stub_mcp2cli):
    """未绑库（无库环境直调第 2 跳）⇒ 注册表不可用 ⇒ 按未命中透传。

    本用例不挂 `mcp_db` fixture——代理正好是未绑状态；解析跳过后 mcp_call 行为
    与 #224 之前逐字一致（这正是增量能力不得打死既有用法的判据）。
    """
    from model.dbModel.base import database_bound

    assert database_bound() is False, "前置被遮：本用例依赖「未绑库」状态"

    ctx = toolRegistry.ToolCallContext(agent_id=1, team_id=1, chat_room=None)
    body = await list_mcp_servers(name_or_target="ctx7", _context=ctx)
    assert body["resolved_from_registry"] is False
    assert "name" not in body
    assert stub_mcp2cli.runtime_argvs[-1][1] == "ctx7"


@pytest.mark.asyncio
async def test_empty_target_passes_through(mcp_db, leader_ctx, loaded_tools, stub_mcp2cli):
    """§3.6-6：空 target 不是解析场景，沿用既有 mcp_invalid_target。"""
    body = await list_mcp_servers(name_or_target="  ", _context=leader_ctx)
    # 纯空白 = 无参 → 列注册表分支
    assert body["success"] is True
    assert "servers" in body


# ---------------------------------------------------------------------------
# 4. 权限（T6）与凭证纪律（1331 / B3）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_basic_agent_console_add_succeeds(
    mcp_db, loaded_tools, stub_mcp2cli
):
    """#277：写入不再要求管理员——普通成员（Write 权限）经 console 调 mcp add 直接成功。"""
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )
    ctx = toolRegistry.ToolCallContext(agent_id=2, team_id=1, chat_room=None)
    call = llmApiUtil.OpenAIToolCall(
        id="tc_basic_add",
        function={
            "name": "console",
            "arguments": json.dumps({"command": "mcp add --name ctx7 --target https://x"}),
        },
    )
    result = await registry.execute_tool_call(call, context=ctx)
    body = result.result
    assert body["success"] is True, body
    assert body["server"]["name"] == "ctx7"


@pytest.mark.asyncio
async def test_basic_agent_direct_write_succeeds_without_admin(mcp_db, loaded_tools):
    """#277 F3 替代判据：**直调面**（不经 console）save / delete 对普通成员成功。

    原 `test_basic_agent_direct_call_gets_admin_denied`（1337 附加判据 1）随
    ADMIN→WRITE 一起删除——它锁的是旧防线；#277 起直调写入同样放行（防线只剩
    全局唯一 name，见 #223 变更稿「前提作废，不得引用 ADMIN 是防线」）。
    """
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )
    ctx = toolRegistry.ToolCallContext(agent_id=2, team_id=1, chat_room=None)
    call = llmApiUtil.OpenAIToolCall(
        id="tc_basic_direct_save",
        function={
            "name": "save_mcp_server",
            "arguments": json.dumps({"name": "ctx7", "kind": "mcp", "target": "https://x"}),
        },
    )
    result = await registry.execute_tool_call(call, context=ctx)
    assert result.result["success"] is True, result.result

    rm = llmApiUtil.OpenAIToolCall(
        id="tc_basic_direct_rm",
        function={"name": "delete_mcp_server", "arguments": json.dumps({"name": "ctx7"})},
    )
    removed = await registry.execute_tool_call(rm, context=ctx)
    assert removed.result["success"] is True, removed.result


@pytest.mark.asyncio
async def test_basic_member_can_rm_and_enable(mcp_db, loaded_tools, stub_mcp2cli):
    """#277 权限判据：普通成员 console 侧完成 rm / enable 并生效。"""
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )
    ctx = toolRegistry.ToolCallContext(agent_id=2, team_id=1, chat_room=None)

    def _call(name: str, command: str) -> llmApiUtil.OpenAIToolCall:
        return llmApiUtil.OpenAIToolCall(
            id=f"tc_{name}",
            function={"name": "console", "arguments": json.dumps({"command": command})},
        )

    # 先由同一普通成员 add（写权限同样放开）
    added = await registry.execute_tool_call(
        _call("add", "mcp add --name ctx7 --kind mcp --target https://x"), context=ctx
    )
    assert added.result["success"] is True, added.result

    disabled = await registry.execute_tool_call(_call("dis", "mcp disable ctx7"), context=ctx)
    assert disabled.result["success"] is True, disabled.result
    enabled = await registry.execute_tool_call(_call("en", "mcp enable ctx7"), context=ctx)
    assert enabled.result["success"] is True, enabled.result

    removed = await registry.execute_tool_call(_call("rm", "mcp rm ctx7"), context=ctx)
    assert removed.result["success"] is True, removed.result
    after = await registry.execute_tool_call(_call("ls", "mcp ls"), context=ctx)
    assert after.result["count"] == 0


@pytest.mark.asyncio
async def test_basic_agent_can_list_registry(mcp_db, loaded_tools):
    """list 归 BASIC：没有 Admin 的 Agent 至少能看。"""
    await _register_server()
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )
    ctx = toolRegistry.ToolCallContext(agent_id=2, team_id=1, chat_room=None)
    call = llmApiUtil.OpenAIToolCall(
        id="tc_basic_ls",
        function={"name": "console", "arguments": json.dumps({"command": "mcp ls"})},
    )
    result = await registry.execute_tool_call(call, context=ctx)
    body = result.result
    assert body["success"] is True, body
    assert body["count"] == 1


@pytest.mark.asyncio
async def test_exception_log_exit_masks_secret(
    mcp_db, leader_ctx, loaded_tools, stub_mcp2cli, monkeypatch, caplog
):
    """#277 F2+F6（38 二轮打回）：异常日志出口的打码必须在**最终输出形态**上。

    上游把完整 argv 回显进异常文案是最常见泄漏形态：返回体走了 `_mask_deep`
    是干净的，但这条 warning 在 `_run_argv_sync` **内部**、外层打码管不到。
    F6 窄口径：先 repr 再打码时，值里的 `'`/`\\` 被转义，字面匹配落空 ⇒ 日志
    留可还原明文。判据（38 定）：同一次调用里，异常日志行**存在**（正向取证），
    且对日志文本的**原始、repr 转义、去转义三形态**全部 0 命中——只搜字面量会假绿。
    本用例同时钉住两条日志出口的次序：argv info 行（先打码后 repr，掩码后无明文
    可转义）与异常 warning 行（打码=最终形态）。
    """
    await _register_server(
        target="https://mcp.example.com/mcp",
        credentials={"headers": {"Authorization": _SECRET, "X-Api-Key": _TRICKY}},
    )

    def _explode(argv: list[str]) -> None:
        raise RuntimeError(f"upstream exploded, argv={argv}")

    monkeypatch.setattr(stub_mcp2cli, "_main_impl", _explode)
    with caplog.at_level(logging.INFO, logger="service.funcToolService.mcpTool"):
        body = await mcp_call(target="ctx7", action="list", _context=leader_ctx)
    assert body["success"] is False, body
    assert body["error_code"] == "mcp2cli_error", body

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "--auth-header" in log_text, log_text  # argv 结构可见
    # 正向（#278）：该 warning 出口的**行文本本身**必须存在——`--auth-header` 由
    # `mcp_call: argv=` 的 info 行就能满足，守不到这条出口；这里断言的串只可能由
    # `except Exception` 分支产生，整行被删 ⇒ 本用例必红。
    assert "mcp2cli 调用异常" in log_text, log_text
    # 反向（F6 判据）：原始值 + repr 形态，在日志原文与去转义文本里都 0 命中
    for value in (_SECRET, _TRICKY):
        for form in _secret_forms(value):
            assert form not in log_text, (value, form, log_text)
            assert form not in _unescape(log_text), (value, form, _unescape(log_text))
    # 返回体同样三形态 0 命中
    for value in (_SECRET, _TRICKY):
        for form in _secret_forms(value):
            assert form not in json.dumps(body), (value, form, body)


@pytest.mark.asyncio
async def test_plain_value_reaches_dispatch_and_never_logs(
    mcp_db, leader_ctx, loaded_tools, stub_mcp2cli, secret_env, caplog
):
    """#277 日志判据：明文值送达分派 argv；日志与返回体里 0 命中（出口打码）。"""
    await _register_server(
        target="https://mcp.example.com/mcp",
        credentials={"headers": {"Authorization": _SECRET}},
    )
    with caplog.at_level(logging.INFO, logger="service.funcToolService.mcpTool"):
        from service.funcToolService.tools import mcp_call

        body = await mcp_call(target="ctx7", action="list", _context=leader_ctx)
    assert body["success"] is True, body

    returned_argv = body["argv"]
    assert _SECRET not in json.dumps(returned_argv), "返回体 argv 不得含明文值"
    # 整返回体（不止 argv 子键）0 命中：堵「先打码、再在出口补一段未打码文本」的
    # 绕开型漏洞（MUT-8 靶子——只查 argv 子键时该变异不红）。
    assert _SECRET not in json.dumps(body), "返回体整体不得含明文值（含新增字段）"
    masked_value = returned_argv[returned_argv.index("--auth-header") + 1]
    assert masked_value == "Authorization:***", masked_value

    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert _SECRET not in log_text, "日志 grep 不到明文值（#277 硬判据）"
    assert "--auth-header" in log_text, "日志仍应能看到 argv 结构（只是值被掩码）"

    # 分派 argv（替身收到的）必须含真实明文值——凭证确实送达上游。
    dispatch_argv = stub_mcp2cli.runtime_argvs[-1]
    assert dispatch_argv[dispatch_argv.index("--auth-header") + 1] == f"Authorization:{_SECRET}"


@pytest.mark.asyncio
async def test_stdio_env_value_verbatim_to_dispatch(
    mcp_db, leader_ctx, loaded_tools, stub_mcp2cli
):
    """env 层（mcp_stdio）：#277 起值是明文，直接注入子进程环境变量，无解析链。"""
    await _register_server(
        kind="mcp_stdio",
        target="npx -y some-server",
        credentials={"env": {"API_KEY": _SECRET}},
    )
    from service.funcToolService.tools import mcp_call

    body = await mcp_call(target="ctx7", action="list", _context=leader_ctx)
    assert body["success"] is True, body
    returned = json.dumps(body["argv"])
    assert _SECRET not in returned, "返回体 argv 不得含明文值"
    assert "--env" in returned

    dispatch = stub_mcp2cli.runtime_argvs[-1]
    value = dispatch[dispatch.index("--env") + 1]
    assert value == f"API_KEY={_SECRET}", "分派 argv 必须收到真实明文值"


@pytest.mark.asyncio
async def test_registry_without_credentials_changes_nothing(
    mcp_db, leader_ctx, loaded_tools, stub_mcp2cli
):
    """硬条件 ③ 旁证：零凭证条目不带任何凭证段，extra 通道照旧。"""
    await _register_server()
    from service.funcToolService.tools import mcp_call

    body = await mcp_call(
        target="ctx7", action="list", extra={"transport": "sse"}, _context=leader_ctx
    )
    assert body["success"] is True, body
    argv = stub_mcp2cli.runtime_argvs[-1]
    assert "--auth-header" not in argv and "--env" not in argv
    assert "transport" in json.dumps(argv)
    assert "--auth-header" not in json.dumps(body["argv"])


# ---------------------------------------------------------------------------
# build_argv 纯函数层（凭证段位置语义）
# ---------------------------------------------------------------------------


def test_build_argv_credential_segments_before_tool_name():
    argv = mcpTool.build_argv(
        kind="mcp",
        target="https://x",
        action="call",
        tool="my-tool",
        args={"a": "1"},
        extra=None,
        credentials={"headers": {"Authorization": _SECRET}},
    )
    # 全局段（含凭证）必须排在工具名之前（上游 _split_at_subcommand 语义）。
    assert argv.index("--auth-header") < argv.index("my-tool")
    assert argv == [
        "--mcp", "https://x", "--json",
        "--auth-header", f"Authorization:{_SECRET}",
        "my-tool", "--a", "1",
    ]


def test_mask_deep_only_touches_secret_values():
    """#277 打码只对**精确明文值**做子串替换：形似但不同的串不受误伤。"""
    argv = [
        "--mcp", f"https://x?ref={_SECRET[:4]}-other", "--json",
        "--auth-header", f"Authorization:{_SECRET}",
        "--list",
    ]
    masked = mcpTool._mask_deep(argv, [_SECRET])
    assert masked[1] == f"https://x?ref={_SECRET[:4]}-other"  # 非整值命中，原样
    assert masked[4] == "Authorization:***"                    # 明文值被替换
    assert masked[3] == "--auth-header"                        # flag 本身不动


# ---------------------------------------------------------------------------
# 5. enable 落地语义（#273 打回补判据——此前两条核心语义零断言，实测变异全绿）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disabled_server_call_reports_mcp_server_disabled(
    mcp_db, leader_ctx, loaded_tools, stub_mcp2cli
):
    """禁用后 `mcp call` 必须报 `mcp_server_disabled`，且不发起真实调用。

    打回补判据：`tools.mcp_call` 的 `if not entry.enable:` 拦截段此前无任何测试
    断言钉住（全仓 grep 仅 1 处注释）。M 变异（删拦截段）⇒ 本用例红：调用照常
    分派、返回 stub 结果、error_code 缺失。
    """
    from service.funcToolService.tools import mcp_call, set_mcp_server_enabled

    await _register_server(kind="mcp")
    disabled = await set_mcp_server_enabled(name="ctx7", enable=False, _context=leader_ctx)
    assert disabled["success"] is True, disabled

    body = await mcp_call(target="ctx7", action="list", _context=leader_ctx)
    assert body["success"] is False, body
    assert body["error_code"] == "mcp_server_disabled", body
    assert body["resolved_from_registry"] is True
    assert body["name"] == "ctx7"
    assert stub_mcp2cli.runtime_argvs == [], "禁用项不得发起真实调用"

    # 重新启用后同一入口恢复可用（拦截是 enable 态的函数，不是一次性的）。
    await set_mcp_server_enabled(name="ctx7", enable=True, _context=leader_ctx)
    revived = await mcp_call(target="ctx7", action="list", _context=leader_ctx)
    assert revived["success"] is True, revived
    assert stub_mcp2cli.runtime_argvs, "重新启用后必须恢复分派"


@pytest.mark.asyncio
async def test_disabled_entry_ls_body_says_enable_false(
    mcp_db, leader_ctx, loaded_tools, stub_mcp2cli
):
    """无参 `mcp ls` 的输出必须把启用/禁用两态标成可区分（工具层 + console 路径同验）。

    PM 18:55 口径②（console 路径部分，18:04 复核定稿）：经 `execute_tool_call` 真走
    `mcp ls`，被禁用那条要带可指认的禁用标记（`enable is False`），且**同一输出里
    启用项不得带该标记**（两个方向都断——只验单向的话，「写死一律打标记」也能过）。

    变异实测（二条，一次到位）：
    · `entry_summary` 删掉 `"enable"` 字段 ⇒ 输出里根本没有禁用标记 ⇒ 红；
    · `entry_summary` 的 enable 改恒 False（一律打标记）⇒ 启用项方向 ⇒ 红。
    恢复 ⇒ 绿。旧用例只断 DB 列与「仍在列表」，返回体两态不可区分（打回实测）。
    """
    from service.funcToolService.tools import set_mcp_server_enabled

    await _register_server(name="ctx7", kind="mcp")  # 将被禁用
    await _register_server(
        name="live-server", kind="mcp", target="https://live.example.com"
    )  # 保持启用
    await set_mcp_server_enabled(name="ctx7", enable=False, _context=leader_ctx)

    async def _assert_two_states_distinguishable(body: dict):
        states = {s["name"]: s["enable"] for s in body["servers"]}
        assert states == {"ctx7": False, "live-server": True}, (
            "ls 输出必须两态可区分（禁用项带标记、启用项不带）：" f"{states}"
        )

    # 工具层：list_mcp_servers() 返回体
    listed = await list_mcp_servers(name_or_target=None, _context=leader_ctx)
    assert listed["success"] is True
    await _assert_two_states_distinguishable(listed)

    # console 路径：真走 execute_tool_call，拿模型实际看到的 `mcp ls` 输出
    registry = _build_registry()
    call = llmApiUtil.OpenAIToolCall(
        id="tc_console_ls_two_states",
        function={"name": "console", "arguments": json.dumps({"command": "mcp ls"})},
    )
    result = await registry.execute_tool_call(call, context=leader_ctx)
    payload = result.result if hasattr(result, "result") else result
    assert payload.get("success") is True, payload
    await _assert_two_states_distinguishable(payload)


@pytest.mark.asyncio
async def test_ls_credentials_masked_two_states_identical(mcp_db, leader_ctx, loaded_tools):
    """打码判据（#277 交付标准 3）：启/禁两态的凭证掩码形态一致，且不含完整值。

    M 变异：`entry_summary` 删掉 `credentials_masked` 字段 ⇒ 本用例红。
    """
    from service.funcToolService.tools import set_mcp_server_enabled

    creds = {"headers": {"Authorization": "Bearer sk-TESTONLY-1234567890"}}
    await _register_server(kind="mcp", credentials=creds)

    enabled_body = await list_mcp_servers(name_or_target=None, _context=leader_ctx)
    await set_mcp_server_enabled(name="ctx7", enable=False, _context=leader_ctx)
    disabled_body = await list_mcp_servers(name_or_target=None, _context=leader_ctx)

    enabled = [s for s in enabled_body["servers"] if s["name"] == "ctx7"][0]
    disabled = [s for s in disabled_body["servers"] if s["name"] == "ctx7"][0]
    expected = {"headers": {"Authorization": "Bearer***"}}
    assert enabled["credentials_masked"] == expected
    assert disabled["credentials_masked"] == expected, "启/禁两态打码形态必须一致"
    assert "sk-TESTONLY-1234567890" not in json.dumps(disabled_body)
