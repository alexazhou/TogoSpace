"""服务生命周期注解：统一接管进程级服务的启动/停止契约。

契约（设计文档 v1.6）：

- ``service_start_wrapper``：管理状态、捕获异常（记录后**原样向上抛**）、确保**只启动一次**、
  失败**必回滚**。``startup`` 允许态 = ``{Idle, Stopped}``；``Starting`` / ``Running`` / ``Stopping`` /
  ``Error`` 再 ``startup`` 一律抛 ``TogoException(error_code="SERVICE_ALREADY_STARTED")``，
  且不执行 body、不改写状态。``Error`` 的恢复路径固定为 ``shutdown() → startup()``。
- ``service_stop_wrapper``：**幂等**停止；仅 ``Idle``（从未启动）/``Stopped``（重复关闭）态 no-op 并告警，
  其余状态（含 ``Error``）都会真正执行释放逻辑并回 ``Stopped``；body 抛错不 re-raise，但会留痕
  （``fine=False`` + ``error``），避免清理失败被静默抹平。

回滚（``on_error``）：

- 每个持有资源/有副作用的服务提供**未被注解装饰**的纯清理协程 ``_reset()``（幂等）；
  ``startup`` 挂 ``on_error=_reset``，``shutdown`` body 复用同一个 ``_reset()``，避免两份清理逻辑漂移。
- 回滚是 **best-effort**：``on_error()`` 自身抛错只 warning，**绝不掩盖原始启动异常**。

> 同步/异步兼容：仓库中存在同步实现的服务方法（如 ``skillService.startup``、``schedulerService.shutdown``），
> 且被业务代码与测试以同步方式调用。注解会保留被装饰函数的同步/异步特性——异步函数返回可 await 的协程，
> 同步函数同步执行——从而在不改动既有调用点的前提下统一生命周期语义。

分层：放在 ``service/`` 层（可依赖 model/constants），避免 ``util`` 层反向依赖 ``model``。硬严格，不设灰度开关。
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from typing import Any, Callable

from constants import ServiceStatus
from exception import TogoException

logger = logging.getLogger(__name__)


def _begin_start(status: Any) -> None:
    """启动前置校验：仅 ``Idle`` / ``Stopped`` 允许启动；否则抛错且不改写状态。"""
    if status.status not in (ServiceStatus.Idle, ServiceStatus.Stopped):
        raise TogoException(
            f"service '{status.name}' cannot startup in state {status.status.name} (shutdown first)",
            error_code="SERVICE_ALREADY_STARTED",
        )
    status.reset_to_status(ServiceStatus.Starting)


def _mark_start_failed(status: Any, exc: BaseException) -> None:
    err = f"service '{status.name}' start failed: {exc!r}"
    status.status = ServiceStatus.Error
    status.fine = False
    status.error = err
    logger.warning(err)


def _mark_started(status: Any) -> None:
    status.status = ServiceStatus.Running
    logger.info("service '%s' started", status.name)


def _should_skip_shutdown(status: Any) -> bool:
    """非运行态的停机兜底：Idle / Stopped 一律 no-op + warning，不抛错。"""
    if status.status == ServiceStatus.Idle:
        logger.warning("service '%s' shutdown before startup, ignored", status.name)
        return True
    if status.status == ServiceStatus.Stopped:
        logger.warning("service '%s' repeated shutdown (already stopped), ignored", status.name)
        return True
    return False


def _finish_shutdown(status: Any, stop_error: str | None) -> None:
    """停机收尾：无论清理成败都回 ``Stopped``；失败时留痕（v1.6 R1）。"""
    status.reset_to_status(ServiceStatus.Stopped)
    if stop_error is not None:
        status.fine = False
        status.error = stop_error


async def _rollback(status: Any, on_error: Callable[[], Any] | None) -> None:
    """best-effort 回滚：吞掉回滚自身异常，绝不掩盖原始启动异常（异步上下文）。"""
    if on_error is None:
        return
    try:
        result = on_error()
        if inspect.iscoroutine(result):
            await result
    except Exception as rollback_err:  # noqa: BLE001 - 回滚失败只告警，不掩盖原始异常
        logger.warning("service '%s' rollback failed: %r", status.name, rollback_err)


def _rollback_sync(status: Any, on_error: Callable[[], Any] | None) -> None:
    """``on_error`` 的同步版本：语义同 :func:`_rollback`，用于同步 ``startup``。"""
    if on_error is None:
        return
    try:
        result = on_error()
        if inspect.iscoroutine(result):
            # 同步 startup 内的异步回滚：尽力执行；无可用循环时退化为告警。
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                result.close()
                logger.warning(
                    "service '%s' rollback 返回协程但已在事件循环内，无法同步执行，已跳过",
                    status.name,
                )
    except Exception as rollback_err:  # noqa: BLE001 - 回滚失败只告警，不掩盖原始异常
        logger.warning("service '%s' rollback failed: %r", status.name, rollback_err)


def service_start_wrapper(
    status: Any,
    on_error: Callable[[], Any] | None = None,
) -> Callable:
    """服务启动注解：管理状态 + 捕获异常(向上抛) + 确保只启动一次 + 失败必回滚。"""

    def decorator(func: Callable) -> Callable:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                _begin_start(status)
                try:
                    result = await func(*args, **kwargs)
                except Exception as e:
                    await _rollback(status, on_error)
                    _mark_start_failed(status, e)
                    raise
                _mark_started(status)
                return result

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            _begin_start(status)
            try:
                result = func(*args, **kwargs)
            except Exception as e:
                _rollback_sync(status, on_error)
                _mark_start_failed(status, e)
                raise
            _mark_started(status)
            return result

        return sync_wrapper

    return decorator


def service_stop_wrapper(status: Any) -> Callable:
    """服务停止注解：幂等（Idle/Stopped 态 no-op + warning）；异常记录但不抛。"""

    def decorator(func: Callable) -> Callable:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                if _should_skip_shutdown(status):
                    return None
                status.status = ServiceStatus.Stopping
                stop_error: str | None = None
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    stop_error = f"service '{status.name}' stop failed: {e!r}"
                    logger.warning(stop_error)
                    return None
                finally:
                    _finish_shutdown(status, stop_error)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if _should_skip_shutdown(status):
                return None
            status.status = ServiceStatus.Stopping
            stop_error: str | None = None
            try:
                return func(*args, **kwargs)
            except Exception as e:
                stop_error = f"service '{status.name}' stop failed: {e!r}"
                logger.warning(stop_error)
                return None
            finally:
                _finish_shutdown(status, stop_error)

        return sync_wrapper

    return decorator
