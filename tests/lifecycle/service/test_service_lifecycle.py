"""Service 生命周期注解化 —— 架构守卫测试。

设计依据：《Service生命周期注解化管理-设计文档.md》v1.5（任务 #39 实现 / #41 守卫）。
契约要点（v1.4 决策 #10 + v1.5 决策 #11）：

- ``startup`` 允许态 = ``{Idle, Stopped}``；``Starting`` / ``Running`` / ``Stopping`` / ``Error``
  再 ``startup`` 一律抛 ``TogoException(error_code="SERVICE_ALREADY_STARTED")``，且不得执行 body。
- ``shutdown`` 幂等且不抛错：``Idle`` / ``Stopped`` 态 no-op + warning；其余态执行清理并回 ``Stopped``。
- ``startup`` 中途失败 → 先 ``on_error()`` 回滚（best-effort、吞异常），再置 ``Error``，再 re-raise 原始异常。
- ``shutdown`` body 抛错 → 不 re-raise、仍回 ``Stopped``，但必须留痕（``fine=False`` + ``error`` 非空），
  不得被 ``reset_to_status(Stopped)`` 静默抹平（v1.6 决策 #12）。

三层守卫：

1. 结构守卫（AST）：12 个服务模块都声明 ``s_status``，且 ``startup`` / ``shutdown`` 均挂了对应注解。
2. 契约守卫（6 态迁移矩阵）：用 stub 协程直接驱动注解，不启任何真实服务与数据库，结果确定。
3. 回滚守卫：失败回滚、原始异常不被掩盖、回滚逻辑与 shutdown 复用。

确定性优先：契约层只看注解语义，不依赖 DB / 网络 / 全局状态。
"""
from __future__ import annotations

import ast
import importlib
import logging
from pathlib import Path

import pytest

from constants import ServiceStatus
from exception import TogoException
from model.coreModel.serviceStatus import ServiceStatusInfo
from service import serviceUtil

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src"

# 注解实现所在模块名，用于钉 caplog 的 logger（serviceUtil 用 logging.getLogger(__name__)）。
_UTIL_LOGGER = "service.serviceUtil"

# 本次范围内必须接入注解的 12 个进程级服务（设计文档 §6）。
# key: 虚线模块名（用于 import），value: 相对 src 的文件路径（用于 AST 解析）。
_SERVICE_MODULES: dict[str, str] = {
    "service.messageBus": "service/messageBus.py",
    "service.llmService.core": "service/llmService/core.py",
    "service.funcToolService.core": "service/funcToolService/core.py",
    "service.ormService": "service/ormService.py",
    "service.persistenceService": "service/persistenceService.py",
    "service.agentService.core": "service/agentService/core.py",
    "service.roomService.core": "service/roomService/core.py",
    "service.schedulerService": "service/schedulerService.py",
    "service.presetService": "service/presetService.py",
    "service.skillService": "service/skillService.py",
    "service.teamService": "service/teamService.py",
    "service.roleTemplateService": "service/roleTemplateService.py",
}

_STARTUP_DECORATOR = "service_start_wrapper"
_SHUTDOWN_DECORATOR = "service_stop_wrapper"

# 持有模块级可变资源、失败时必须回滚的服务（v1.5 §4.4：必须传 on_error=_reset）。
# 纯 no-op 服务（llm / persistence / preset / team / roleTemplate）无资源可回滚，不在此列。
_RESOURCE_SERVICES: frozenset[str] = frozenset(
    {
        "service.messageBus",
        "service.funcToolService.core",
        "service.ormService",
        "service.agentService.core",
        "service.roomService.core",
        "service.schedulerService",
        "service.skillService",
    }
)

# 服务「活跃中」的状态集合：模块导入期 / 读取时不得停留在这里（只能 Idle 或 Stopped）。
_ACTIVE_STATES = (
    ServiceStatus.Starting,
    ServiceStatus.Running,
    ServiceStatus.Stopping,
    ServiceStatus.Error,
)


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------

def _parse(rel_path: str) -> ast.Module:
    return ast.parse((_SRC / rel_path).read_text(encoding="utf-8"), filename=rel_path)


def _find_module_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _startup_decorator_call(tree: ast.Module) -> ast.Call | None:
    """取 ``startup`` 上 ``@service_start_wrapper(...)`` 的 Call 节点（取不到返回 None）。"""
    fn = _find_module_function(tree, "startup")
    if fn is None:
        return None
    for node in fn.decorator_list:
        if _decorator_name(node) == _STARTUP_DECORATOR and isinstance(node, ast.Call):
            return node
    return None


def _decorator_name(node: ast.expr) -> str | None:
    """从注解节点里取出被调用的名字（兼容 ``@f`` / ``@m.f`` / ``@f(...)``）。"""
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _returns_when_db_already_bound(fn: ast.AST) -> bool:
    """检测 ``if _db is not None: return`` 这类静默早退守卫。"""
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_none_check = (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.IsNot)
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None
        )
        if is_none_check and len(node.body) == 1 and isinstance(node.body[0], ast.Return):
            return True
    return False


def _warning_messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


# ----------------------------------------------------------------------
# 1. 结构守卫（AST）
# ----------------------------------------------------------------------

def test_all_services_declare_s_status():
    """12 个服务模块都必须声明模块级 ``s_status``（ServiceStatusInfo），且不得停留在活跃态。

    断言放宽为「非活跃态」而非严格 ``Idle``：同 worker 内若前序测试类启动又停机过某服务，
    ``s_status.status`` 会是 ``Stopped``（合法终态），严格断言 Idle 会 flaky。
    """
    problems = []
    for dotted in _SERVICE_MODULES:
        module = importlib.import_module(dotted)
        obj = getattr(module, "s_status", None)
        if not isinstance(obj, ServiceStatusInfo):
            problems.append(f"{dotted}: s_status 缺失或类型错误（实际 {type(obj).__name__}）")
        elif obj.status in _ACTIVE_STATES:
            problems.append(f"{dotted}: s_status.status={obj.status.name}，读取时不应停留在活跃态")
    assert not problems, "以下服务的 s_status 未正确声明：\n" + "\n".join(problems)


def test_resource_services_pass_on_error_rollback():
    """R4：持有模块级可变资源的服务必须挂 ``on_error=_reset``（防未来新服务漏传）。"""
    problems = []
    for dotted in sorted(_RESOURCE_SERVICES):
        tree = _parse(_SERVICE_MODULES[dotted])
        call = _startup_decorator_call(tree)
        if call is None:
            problems.append(f"{_SERVICE_MODULES[dotted]}: startup() 未挂 @{_STARTUP_DECORATOR}(...)")
            continue
        on_error = [kw for kw in call.keywords if kw.arg == "on_error"]
        if not on_error or isinstance(on_error[0].value, ast.Constant) and on_error[0].value.value is None:
            problems.append(
                f"{_SERVICE_MODULES[dotted]}: 持有模块级资源却未传 on_error=_reset，"
                "startup 失败时资源无人回收（设计文档 v1.5 §4.4）"
            )
    assert not problems, "以下资源型服务缺少失败回滚钩子：\n" + "\n".join(problems)


def test_resource_services_share_undecorated_reset_with_shutdown():
    """v1.5 §4.4：``_reset()`` 必须是未被注解装饰的纯清理协程，且被 ``shutdown`` 复用。"""
    problems = []
    for dotted in sorted(_RESOURCE_SERVICES):
        rel_path = _SERVICE_MODULES[dotted]
        tree = _parse(rel_path)
        reset_fn = _find_module_function(tree, "_reset")
        if reset_fn is None:
            problems.append(f"{rel_path}: 缺少模块级 _reset()")
            continue
        if reset_fn.decorator_list:
            problems.append(f"{rel_path}: _reset() 不应挂注解（须可在回滚与 shutdown 中复用）")
        # shutdown body 必须复用 _reset()，避免两份清理逻辑漂移
        shutdown_fn = _find_module_function(tree, "shutdown")
        reused = shutdown_fn is not None and any(
            isinstance(node, ast.Call) and _decorator_name(node.func) == "_reset" and not node.args and not node.keywords
            for node in ast.walk(shutdown_fn)
        )
        if not reused:
            problems.append(f"{rel_path}: shutdown() 未复用 _reset()（清理逻辑有漂移风险）")
    assert not problems, "以下服务的 _reset 约定不满足 v1.5 §4.4：\n" + "\n".join(problems)


def test_startup_and_shutdown_are_annotation_wrapped():
    """每个服务的 ``startup`` / ``shutdown`` 都必须挂对应生命周期注解。"""
    problems = []
    for dotted, rel_path in _SERVICE_MODULES.items():
        tree = _parse(rel_path)
        for func_name, expected in (("startup", _STARTUP_DECORATOR), ("shutdown", _SHUTDOWN_DECORATOR)):
            fn = _find_module_function(tree, func_name)
            if fn is None:
                problems.append(f"{rel_path}: 缺少模块级 {func_name}()")
                continue
            decorators = {_decorator_name(d) for d in fn.decorator_list}
            if expected not in decorators:
                problems.append(
                    f"{rel_path}: {func_name}() 未挂 @{expected}（实际注解 {sorted(d for d in decorators if d)}）"
                )
    assert not problems, "以下服务的生命周期方法未接入注解：\n" + "\n".join(problems)


def test_ormservice_has_no_silent_early_return():
    """方案 A：ormService.startup 不得保留 ``if _db is not None: return`` 静默早退。"""
    tree = _parse(_SERVICE_MODULES["service.ormService"])
    startup = _find_module_function(tree, "startup")
    assert startup is not None, "service/ormService.py 缺少模块级 startup()"
    assert not _returns_when_db_already_bound(startup), (
        "service/ormService.py: startup() 仍存在 `if _db is not None: return` 静默早退；"
        "二次启动必须由注解统一抛 SERVICE_ALREADY_STARTED（设计文档 §3.3 方案 A）"
    )


# ----------------------------------------------------------------------
# 2. 契约守卫（6 态迁移矩阵）
# ----------------------------------------------------------------------

@pytest.mark.parametrize("initial", [ServiceStatus.Idle, ServiceStatus.Stopped])
async def test_startup_allowed_from_idle_and_stopped(initial):
    status = ServiceStatusInfo(name="stub")
    status.status = initial
    entered: list[str] = []

    @serviceUtil.service_start_wrapper(status)
    async def startup() -> str:
        entered.append("startup")
        return "ok"

    assert await startup() == "ok"
    assert entered == ["startup"]
    assert status.status is ServiceStatus.Running
    assert status.fine is True


@pytest.mark.parametrize(
    "blocked",
    [ServiceStatus.Starting, ServiceStatus.Running, ServiceStatus.Stopping, ServiceStatus.Error],
)
async def test_startup_rejected_from_any_other_state(blocked):
    """严格口径：非 {Idle, Stopped} 一律抛 SERVICE_ALREADY_STARTED，且不执行 body、不改写状态。"""
    status = ServiceStatusInfo(name="stub")
    status.status = blocked
    entered: list[str] = []

    @serviceUtil.service_start_wrapper(status)
    async def startup() -> None:
        entered.append("startup")

    with pytest.raises(TogoException) as excinfo:
        await startup()

    assert excinfo.value.error_code == "SERVICE_ALREADY_STARTED"
    assert entered == [], "被拒绝的 startup 不得执行 body"
    assert status.status is blocked, "被拒绝的 startup 不得改写状态"


async def test_startup_failure_sets_error_and_reraises_original():
    status = ServiceStatusInfo(name="stub")
    boom = RuntimeError("db down")

    @serviceUtil.service_start_wrapper(status)
    async def startup() -> None:
        raise boom

    with pytest.raises(RuntimeError) as excinfo:
        await startup()

    assert excinfo.value is boom, "必须原样 re-raise 原始异常（fail loud）"
    assert status.status is ServiceStatus.Error
    assert status.fine is False
    assert status.error is not None and "start failed" in status.error


@pytest.mark.parametrize(
    ("initial", "fragment"),
    [
        (ServiceStatus.Idle, "shutdown before startup"),
        (ServiceStatus.Stopped, "repeated shutdown"),
    ],
)
async def test_shutdown_is_noop_with_warning_on_idle_and_stopped(initial, fragment, caplog):
    status = ServiceStatusInfo(name="stub")
    status.status = initial
    entered: list[str] = []

    @serviceUtil.service_stop_wrapper(status)
    async def shutdown() -> None:
        entered.append("shutdown")

    with caplog.at_level(logging.WARNING, logger=_UTIL_LOGGER):
        result = await shutdown()

    assert result is None
    assert entered == [], "Idle/Stopped 态 shutdown 必须 no-op，不得执行释放逻辑"
    assert status.status is initial, "no-op 的 shutdown 不得改写状态"
    messages = _warning_messages(caplog)
    assert any(fragment in m for m in messages), f"未捕获到告警 '{fragment}'，实际告警：{messages}"


@pytest.mark.parametrize(
    "active",
    [ServiceStatus.Starting, ServiceStatus.Running, ServiceStatus.Stopping, ServiceStatus.Error],
)
async def test_shutdown_cleans_from_active_states(active, caplog):
    """Error 态也必须真正执行清理并回落 Stopped（Error 恢复路径 = shutdown → startup）。"""
    status = ServiceStatusInfo(name="stub")
    status.status = active
    entered: list[str] = []

    @serviceUtil.service_stop_wrapper(status)
    async def shutdown() -> None:
        entered.append("shutdown")

    with caplog.at_level(logging.WARNING, logger=_UTIL_LOGGER):
        result = await shutdown()

    assert result is None
    assert entered == ["shutdown"], "非 Idle/Stopped 态 shutdown 必须执行清理"
    assert status.status is ServiceStatus.Stopped
    assert _warning_messages(caplog) == [], "活跃态 shutdown 不应产生告警"


async def test_shutdown_swallows_body_exception_but_still_reaches_stopped(caplog):
    status = ServiceStatusInfo(name="stub")
    status.status = ServiceStatus.Running

    @serviceUtil.service_stop_wrapper(status)
    async def shutdown() -> None:
        raise RuntimeError("close failed")

    with caplog.at_level(logging.WARNING, logger=_UTIL_LOGGER):
        result = await shutdown()

    assert result is None, "shutdown 不抛错"
    assert status.status is ServiceStatus.Stopped, "即使 body 失败也必须回 Stopped"
    assert any("stop failed" in m for m in _warning_messages(caplog))
    # R1（v1.6 决策 #12）：清理失败必须留痕，不得被 reset_to_status(Stopped) 静默抹平。
    # 留痕写在 finally 的 reset 之后，且会在下一次 startup 的 reset_to_status(Starting) 时被清掉，
    # 因此必须在 shutdown 返回后立即断言（此处即是）。
    assert status.fine is False, "清理失败必须留痕：fine 应为 False"
    assert status.error is not None and "stop failed" in status.error, (
        f"清理失败必须留痕：error 应含 'stop failed'，实际 {status.error!r}"
    )


# ----------------------------------------------------------------------
# 3. 回滚守卫（v1.5 on_error 钩子）
# ----------------------------------------------------------------------

async def test_startup_failure_invokes_on_error_rollback():
    status = ServiceStatusInfo(name="stub")
    rollback: list[str] = []
    boom = RuntimeError("bind 之后失败")

    async def _reset() -> None:
        rollback.append("reset")

    @serviceUtil.service_start_wrapper(status, on_error=_reset)
    async def startup() -> None:
        raise boom

    with pytest.raises(RuntimeError) as excinfo:
        await startup()

    assert rollback == ["reset"], "startup 失败必须先执行 on_error 回滚"
    assert excinfo.value is boom
    assert status.status is ServiceStatus.Error


async def test_on_error_failure_does_not_mask_original(caplog):
    status = ServiceStatusInfo(name="stub")
    boom = RuntimeError("原始启动异常")

    async def _reset() -> None:
        raise ValueError("rollback exploded")

    @serviceUtil.service_start_wrapper(status, on_error=_reset)
    async def startup() -> None:
        raise boom

    with caplog.at_level(logging.WARNING, logger=_UTIL_LOGGER):
        with pytest.raises(RuntimeError) as excinfo:
            await startup()

    assert excinfo.value is boom, "回滚自身失败不得掩盖原始启动异常"
    assert status.status is ServiceStatus.Error
    assert any("rollback failed" in m for m in _warning_messages(caplog)), (
        f"回滚失败必须留下 warning，实际告警：{_warning_messages(caplog)}"
    )


# ----------------------------------------------------------------------
# 4. 同步分支守卫
#    仓库存在同步实现（skillService.startup / schedulerService.shutdown 等），
#    注解必须保留同步/异步特性，同步分支的契约与异步分支完全一致。
# ----------------------------------------------------------------------

@pytest.mark.parametrize("initial", [ServiceStatus.Idle, ServiceStatus.Stopped])
def test_sync_startup_allowed_from_idle_and_stopped(initial):
    status = ServiceStatusInfo(name="stub-sync")
    status.status = initial
    entered: list[str] = []

    @serviceUtil.service_start_wrapper(status)
    def startup() -> str:
        entered.append("startup")
        return "ok"

    assert startup() == "ok", "同步 startup 必须直接返回结果（不得变成协程）"
    assert entered == ["startup"]
    assert status.status is ServiceStatus.Running


@pytest.mark.parametrize("blocked", _ACTIVE_STATES)
def test_sync_startup_rejected_from_any_other_state(blocked):
    status = ServiceStatusInfo(name="stub-sync")
    status.status = blocked
    entered: list[str] = []

    @serviceUtil.service_start_wrapper(status)
    def startup() -> None:
        entered.append("startup")

    with pytest.raises(TogoException) as excinfo:
        startup()

    assert excinfo.value.error_code == "SERVICE_ALREADY_STARTED"
    assert entered == []
    assert status.status is blocked


def test_sync_startup_failure_uses_sync_rollback():
    status = ServiceStatusInfo(name="stub-sync")
    rollback: list[str] = []
    boom = RuntimeError("sync boom")

    def _reset() -> None:
        rollback.append("reset")

    @serviceUtil.service_start_wrapper(status, on_error=_reset)
    def startup() -> None:
        raise boom

    with pytest.raises(RuntimeError) as excinfo:
        startup()

    assert rollback == ["reset"]
    assert excinfo.value is boom
    assert status.status is ServiceStatus.Error


def test_sync_startup_failure_without_on_error_still_marks_error():
    status = ServiceStatusInfo(name="stub-sync")

    @serviceUtil.service_start_wrapper(status)
    def startup() -> None:
        raise RuntimeError("sync boom")

    with pytest.raises(RuntimeError):
        startup()

    assert status.status is ServiceStatus.Error
    assert status.fine is False


def test_sync_startup_runs_async_on_error_when_no_running_loop():
    """同步 startup 内遇到协程回滚：无可用事件循环时用 ``asyncio.run`` 兜底执行。"""
    status = ServiceStatusInfo(name="stub-sync")
    rollback: list[str] = []

    async def _reset() -> None:
        rollback.append("reset")

    @serviceUtil.service_start_wrapper(status, on_error=_reset)
    def startup() -> None:
        raise RuntimeError("sync boom")

    with pytest.raises(RuntimeError):
        startup()

    assert rollback == ["reset"], "无事件循环时协程回滚必须被真正执行"


def test_sync_on_error_failure_does_not_mask_original(caplog):
    """同步回滚自身抛错：仅 warning，原始启动异常照常向上抛。"""
    status = ServiceStatusInfo(name="stub-sync")
    boom = RuntimeError("原始同步启动异常")

    def _reset() -> None:
        raise ValueError("sync rollback exploded")

    @serviceUtil.service_start_wrapper(status, on_error=_reset)
    def startup() -> None:
        raise boom

    with caplog.at_level(logging.WARNING, logger=_UTIL_LOGGER):
        with pytest.raises(RuntimeError) as excinfo:
            startup()

    assert excinfo.value is boom
    assert status.status is ServiceStatus.Error
    assert any("rollback failed" in m for m in _warning_messages(caplog))


async def test_sync_startup_skips_async_on_error_inside_running_loop(caplog):
    """已在事件循环内时，同步分支无法执行协程回滚 → 跳过 + 告警，仍不掩盖原始异常。"""
    status = ServiceStatusInfo(name="stub-sync")
    rollback: list[str] = []
    boom = RuntimeError("sync boom")

    async def _reset() -> None:
        rollback.append("reset")

    @serviceUtil.service_start_wrapper(status, on_error=_reset)
    def startup() -> None:
        raise boom

    with caplog.at_level(logging.WARNING, logger=_UTIL_LOGGER):
        with pytest.raises(RuntimeError) as excinfo:
            startup()

    assert excinfo.value is boom
    assert rollback == [], "事件循环内不得同步执行协程回滚"
    assert status.status is ServiceStatus.Error
    assert any("无法同步执行" in m for m in _warning_messages(caplog))


@pytest.mark.parametrize(
    ("initial", "fragment"),
    [
        (ServiceStatus.Idle, "shutdown before startup"),
        (ServiceStatus.Stopped, "repeated shutdown"),
    ],
)
def test_sync_shutdown_is_noop_with_warning_on_idle_and_stopped(initial, fragment, caplog):
    status = ServiceStatusInfo(name="stub-sync")
    status.status = initial
    entered: list[str] = []

    @serviceUtil.service_stop_wrapper(status)
    def shutdown() -> None:
        entered.append("shutdown")

    with caplog.at_level(logging.WARNING, logger=_UTIL_LOGGER):
        result = shutdown()

    assert result is None
    assert entered == []
    assert status.status is initial
    assert any(fragment in m for m in _warning_messages(caplog))


@pytest.mark.parametrize("active", _ACTIVE_STATES)
def test_sync_shutdown_cleans_from_active_states(active, caplog):
    status = ServiceStatusInfo(name="stub-sync")
    status.status = active
    entered: list[str] = []

    @serviceUtil.service_stop_wrapper(status)
    def shutdown() -> None:
        entered.append("shutdown")

    with caplog.at_level(logging.WARNING, logger=_UTIL_LOGGER):
        result = shutdown()

    assert result is None
    assert entered == ["shutdown"]
    assert status.status is ServiceStatus.Stopped
    assert _warning_messages(caplog) == []


def test_sync_shutdown_swallows_body_exception_and_leaves_trace(caplog):
    status = ServiceStatusInfo(name="stub-sync")
    status.status = ServiceStatus.Running

    @serviceUtil.service_stop_wrapper(status)
    def shutdown() -> None:
        raise RuntimeError("sync close failed")

    with caplog.at_level(logging.WARNING, logger=_UTIL_LOGGER):
        result = shutdown()

    assert result is None, "shutdown 不抛错"
    assert status.status is ServiceStatus.Stopped
    assert any("stop failed" in m for m in _warning_messages(caplog))
    assert status.fine is False
    assert status.error is not None and "stop failed" in status.error


async def test_error_state_recovers_via_shutdown_then_startup():
    """Error → shutdown() → startup() 必须能恢复 Running。"""
    status = ServiceStatusInfo(name="stub")
    boom = RuntimeError("first attempt fails")
    attempts: list[int] = []

    async def _reset() -> None:
        attempts.append(-1)

    @serviceUtil.service_start_wrapper(status, on_error=_reset)
    async def startup() -> None:
        attempts.append(1)
        if len([a for a in attempts if a == 1]) == 1:
            raise boom

    @serviceUtil.service_stop_wrapper(status)
    async def shutdown() -> None:
        attempts.append(-2)

    with pytest.raises(RuntimeError):
        await startup()
    assert status.status is ServiceStatus.Error

    await shutdown()
    assert status.status is ServiceStatus.Stopped

    await startup()
    assert status.status is ServiceStatus.Running
