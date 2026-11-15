"""全局 MCP server 注册表 service（#273：Operator 2026-11-15 17:54 需求变更）。

分层边界：命令面只转发；name 解析在 `tools.mcp_call` 入口；**业务规则在本层**；
工具薄壳只做上下文提取、参数转发、异常转 `toolResult`。

#273 相对 #224 半成品的三处变更：①去 team_id（全局共享，唯一约束=全局唯一 name）；
②alias → name（不留兼容别名，功能从未发布）；③新增 enable（禁用≠删除：`mcp call`
报「已禁用」、`mcp ls` 仍列出并标注；注册表本身不进 Agent 工具列表，「禁用⇒工具
列表消失」无结构落地）。

凭证纪律（#277，Operator 2026-11-15 20:21/20:22 需求变更，覆盖 #273 的「只存引用」）：
- **值原样存**（明文），不再校验/要求任何前缀（`env:` / `file:` / `keychain:` 三种
  引用语法一并移除，不做兼容）；结构校验保留（headers/env 两层 + kind 匹配）；
- 明文由此会进 argv ⇒ **脱敏改为「出口打码」**：日志、异常文案、返回体在输出前
  把凭证值替换为 `***`（`mcpTool._mask_secret_values` / `mask_config_for_display`），
  本层不负责输出面；
- 解析链（`env:VAR` → 环境变量值）已删除（#277）：注入直接用字面值。
"""
from __future__ import annotations

import logging
import re

import peewee

from exception import TogoException
from model.dbModel.gtMcpServer import (
    GtMcpServer,
    mask_config_for_display,
    mask_target,
)
from dal.db import gtMcpServerManager

logger = logging.getLogger(__name__)

#: name 格式（沿 #224 半成品的别名格式）：小写字母开头，`[a-z0-9_-]`，≤32 字符。
#: 依据是解析器边界（shlex / `--` 前缀当选项 / `-h` 全局短路），不是偏好。
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

#: kind 合法值（`tools.mcp_call` 同一口径，`mcpTool._SOURCE_FLAGS` 的键集）。
VALID_KINDS = ("openapi", "mcp", "mcp_stdio", "graphql")

#: HTTP 类 kind：headers 凭证（`--auth-header`）只对它们有意义。
_HTTP_KINDS = ("openapi", "mcp", "graphql")
#: stdio 类 kind：env 凭证（`--env`，注入子进程环境）只对它有意义。
_STDIO_KIND = "mcp_stdio"

_ERROR_NAME_INVALID = "mcp_invalid_name"
_ERROR_KIND_INVALID = "mcp_invalid_kind"
_ERROR_TARGET_INVALID = "mcp_invalid_target"
_ERROR_NAME_EXISTS = "mcp_name_exists"
_ERROR_NAME_NOT_FOUND = "mcp_name_not_found"
_ERROR_SERVER_DISABLED = "mcp_server_disabled"
_ERROR_CONFIG_INVALID = "mcp_invalid_credentials"
_ERROR_CREDENTIAL_KIND_MISMATCH = "mcp_credential_kind_mismatch"


def _fail(error_code: str, message: str) -> TogoException:
    return TogoException(message, error_code=error_code)


def validate_name(name: str) -> str:
    """name 格式校验；通过返回 strip 后的值，不通过抛 `mcp_invalid_name`。"""
    text = str(name or "").strip()
    if not _NAME_RE.match(text):
        raise _fail(
            _ERROR_NAME_INVALID,
            "name 不合法：须为小写字母开头、仅含小写字母/数字/下划线/连字符、"
            f"最长 32 字符（收到 {text!r}）。示例：ctx7、github-mcp。",
        )
    return text


def validate_kind(kind: str) -> str:
    text = str(kind or "").strip()
    if text not in VALID_KINDS:
        raise _fail(
            _ERROR_KIND_INVALID,
            f"kind 只支持 {' / '.join(VALID_KINDS)}，收到 {text!r}。"
            "（注册表默认 mcp；openapi 需显式指定。）",
        )
    return text


def validate_target(target: str) -> str:
    text = str(target or "").strip()
    if not text:
        raise _fail(_ERROR_TARGET_INVALID, "target 不能为空（填 spec 路径 / MCP URL / stdio 启动命令 / GraphQL URL）。")
    return text


def validate_config(config: dict | None, kind: str) -> dict:
    """凭证**结构**校验（#277：值原样存，不再校验前缀）。返回归一后的存储形态。

    - `{}` / `None` ⇒ `{}`（空凭证统一存 `{}`）；
    - 结构只能是 `{"headers": {...}, "env": {...}}` 两层；**值原样存**（明文，
      不做任何前缀/形态校验——Operator 20:21 明确改为直接存值）；
    - headers 层只配 HTTP 类 kind、env 层只配 `mcp_stdio`（错配 ⇒
      `mcp_credential_kind_mismatch`）——静默存下调用时永不生效的凭证，比报错更坏。

    值是明文 ⇒ 脱敏责任在**输出面**（日志/返回体/异常文案打码，见模块 docstring），
    校验层不做打码（存的就是值，展示才打码）。
    """
    if not config:
        return {}
    if not isinstance(config, dict):
        raise _fail(_ERROR_CONFIG_INVALID, "config 必须是对象：{\"headers\":{名:值},\"env\":{变量:值}}。")

    unknown_layers = sorted(set(config) - {"headers", "env"})
    if unknown_layers:
        raise _fail(
            _ERROR_CONFIG_INVALID,
            f"config 只支持 headers / env 两层，收到未知层：{unknown_layers}。",
        )

    normalized: dict[str, dict[str, str]] = {}
    for layer in ("headers", "env"):
        values = config.get(layer)
        if values in (None, {}, []):
            continue
        if not isinstance(values, dict):
            raise _fail(_ERROR_CONFIG_INVALID, f"config.{layer} 必须是「名称 → 值」的对象。")
        layer_out: dict[str, str] = {}
        for name, value in values.items():
            if not isinstance(value, str) or not value.strip():
                raise _fail(_ERROR_CONFIG_INVALID, f"config.{layer} 的值必须是非空字符串。")
            layer_out[str(name)] = value.strip()
        normalized[layer] = layer_out

    if "headers" in normalized and kind not in _HTTP_KINDS:
        raise _fail(
            _ERROR_CREDENTIAL_KIND_MISMATCH,
            f"headers 凭证只对 HTTP 类 kind（{' / '.join(_HTTP_KINDS)}）有意义，当前 kind={kind}。",
        )
    if "env" in normalized and kind != _STDIO_KIND:
        raise _fail(
            _ERROR_CREDENTIAL_KIND_MISMATCH,
            f"env 凭证只对 kind=mcp_stdio 有意义（注入子进程环境变量），当前 kind={kind}。",
        )
    return normalized


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------


async def resolve_name(name: str) -> GtMcpServer | None:
    """按传入值精确查 `name` 列；命中返回注册项（**含禁用项**，禁用判断由调用方做），
    未命中返回 None（透传语义）。

    `mcp call` 与 `mcp ls <name>` 两个解析点**必须**复用本函数（C3：同一语义两处实现
    必然漂移）。
    """
    text = str(name or "").strip()
    if not text:
        return None
    return await gtMcpServerManager.get_by_name(text)


def entry_summary(entry: GtMcpServer) -> dict:
    """注册项的**脱敏**摘要（ls / rm / name_exists 附带说明共用同一份形态）。

    `config_masked`（#277）：值已明文存储，查看面统一给掩码（保留少量前缀
    便于识别、主体打码）；启用/禁用条目走同一份代码路径，打码形态天然一致。
    """
    return {
        "name": entry.name,
        "kind": entry.kind,
        "target_masked": mask_target(entry.target),
        "note": entry.note,
        "enable": bool(entry.enable),
        "config_masked": mask_config_for_display(entry.config or {}),
        "has_config": bool(entry.config),
        "updated_at": str(entry.updated_at) if entry.updated_at else None,
    }


def config_of(entry: GtMcpServer) -> dict:
    """从注册项取出凭证（#277：**值原样**，注入时直接用字面值，无解析链）。"""
    config = entry.config or {}
    return {
        "headers": dict(config.get("headers") or {}),
        "env": dict(config.get("env") or {}),
    }


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------


async def save_server(
    *,
    name: str,
    kind: str = "mcp",
    target: str,
    note: str = "",
    config: dict | None = None,
    overwrite: bool = False,
    created_by: int = 0,
) -> GtMcpServer:
    """登记 / 显式覆盖一个注册项（全局，无 team 作用域）。insert-only 是默认路径。

    正确性不依赖「先查后插」：并发第二写由全局唯一索引 `ux_mcp_servers_name` 兜底，
    `IntegrityError` 统一转 `mcp_name_exists`（与预检语义一致）。`overwrite` 只覆盖
    kind/target/note/config，**不动 enable**（禁用状态属运维态，覆盖登记不改它）。
    """
    name_n = validate_name(name)
    kind_n = validate_kind(kind)
    target_n = validate_target(target)
    config_n = validate_config(config, kind_n)

    existing = await gtMcpServerManager.get_by_name(name_n)
    if existing is not None and not overwrite:
        raise _fail(
            _ERROR_NAME_EXISTS,
            f"name {name_n!r} 已登记（{entry_summary(existing)}）。"
            "覆盖已有登记必须显式 overwrite=true。",
        )

    if existing is not None:
        existing.kind = kind_n
        existing.target = target_n
        existing.note = note or ""
        existing.config = config_n
        await gtMcpServerManager.update_entry(
            existing, ["kind", "target", "note", "config"]
        )
        logger.info("mcp registry: name %s 已覆盖", name_n)
        return existing

    entry = GtMcpServer(
        name=name_n,
        kind=kind_n,
        target=target_n,
        note=note or "",
        config=config_n,
        enable=1,
        created_by=created_by,
    )
    try:
        await gtMcpServerManager.insert_entry(entry)
    except peewee.IntegrityError as exc:
        # 唯一索引冲突统一转业务错误码：并发第二写与预检语义一致。
        raise _fail(
            _ERROR_NAME_EXISTS,
            f"name {name_n!r} 已登记（并发写入撞全局唯一索引）。覆盖须显式 overwrite=true。",
        ) from exc
    logger.info("mcp registry: name %s 已登记（kind=%s）", name_n, kind_n)
    return entry


async def delete_server(name: str) -> GtMcpServer:
    """只按 name 精确删（不支持按 target / 前缀）；不存在抛 `mcp_name_not_found`。"""
    name_n = validate_name(name)
    existing = await gtMcpServerManager.get_by_name(name_n)
    if existing is None:
        raise _fail(
            _ERROR_NAME_NOT_FOUND,
            f"注册表里没有 name {name_n!r}。mcp ls 可先看现有登记项。",
        )
    await gtMcpServerManager.delete_by_name(name_n)
    logger.info("mcp registry: name %s 已删除", name_n)
    return existing


async def set_enabled(name: str, enable: bool) -> GtMcpServer:
    """启用 / 禁用一个注册项（#273）。

    禁用 ≠ 删除：条目保留，`mcp ls` 仍列出并标注；禁用后 `mcp call` 明确报
    `mcp_server_disabled`（不是「不存在」）。注意：注册表本身不进 Agent 可用工具
    列表，「禁用⇒工具从列表消失」无结构落地（#273 未做）。不存在抛
    `mcp_name_not_found`。
    """
    name_n = validate_name(name)
    existing = await gtMcpServerManager.get_by_name(name_n)
    if existing is None:
        raise _fail(
            _ERROR_NAME_NOT_FOUND,
            f"注册表里没有 name {name_n!r}。mcp ls 可先看现有登记项。",
        )
    existing.enable = 1 if enable else 0
    await gtMcpServerManager.update_entry(existing, ["enable"])
    logger.info("mcp registry: name %s enable → %s", name_n, bool(enable))
    return existing


async def list_servers() -> list[GtMcpServer]:
    """全部注册项（含禁用——禁用≠删除，要看得见才好启用）。"""
    return await gtMcpServerManager.list_all()
