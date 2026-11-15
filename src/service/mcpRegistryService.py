"""全局 MCP server 注册表 service（#273：Operator 2026-11-15 17:54 需求变更）。

分层边界：命令面只转发；name 解析在 `tools.mcp_call` 入口；**业务规则在本层**；
工具薄壳只做上下文提取、参数转发、异常转 `toolResult`。

#273 相对 #224 半成品的三处变更：①去 team_id（全局共享，唯一约束=全局唯一 name）；
②alias → name（不留兼容别名，功能从未发布）；③新增 enable（禁用≠删除：`mcp call`
报「已禁用」、`mcp ls` 仍列出并标注；注册表本身不进 Agent 工具列表，「禁用⇒工具
列表消失」无结构落地）。

凭证纪律（设计稿 §4，1337 附加判据 2）：
- 注册表只存**引用**（`env:VAR` 形态），明文一律拒绝（`mcp_inline_credential_refused`）；
- `file:` / `keychain:` 报「未实现」，**不降级成明文**（`mcp_credential_ref_unimplemented`）；
- 解析（`env:VAR` → 环境变量值）发生在 mcpTool 的日志**之后**、分派调用之前
  （1331 硬条件 ⅱ），本层不解析、不持有任何凭证值。
"""
from __future__ import annotations

import logging
import re

import peewee

from exception import TogoException
from model.dbModel.gtMcpServer import (
    GtMcpServer,
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
_ERROR_CREDENTIALS_INVALID = "mcp_invalid_credentials"
_ERROR_CREDENTIAL_REFUSED = "mcp_inline_credential_refused"
_ERROR_CREDENTIAL_UNIMPLEMENTED = "mcp_credential_ref_unimplemented"
_ERROR_CREDENTIAL_REF_INVALID = "mcp_invalid_credential_ref"
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


def validate_credentials(credentials: dict | None, kind: str) -> dict:
    """凭证结构校验（设计稿 §4.3 B1/B6）。只存引用；返回归一后的存储形态。

    - `{}` / `None` ⇒ `{}`（空凭证统一存 `{}`，E1）；
    - 结构只能是 `{"headers": {...}, "env": {...}}` 两层，一个 key 一个引用；
    - 值必须是 `env:` 引用（首版唯一实现）；明文 ⇒ `mcp_inline_credential_refused`；
      `file:` / `keychain:` ⇒ 未实现，不降级；其他前缀 ⇒ `mcp_invalid_credential_ref`；
    - headers 层只配 HTTP 类 kind、env 层只配 `mcp_stdio`（错配 ⇒
      `mcp_credential_kind_mismatch`）——静默存下调用时永不生效的凭证，比报错更坏。
    """
    if not credentials:
        return {}
    if not isinstance(credentials, dict):
        raise _fail(_ERROR_CREDENTIALS_INVALID, "credentials 必须是对象：{\"headers\":{名:引用},\"env\":{变量:引用}}。")

    unknown_layers = sorted(set(credentials) - {"headers", "env"})
    if unknown_layers:
        raise _fail(
            _ERROR_CREDENTIALS_INVALID,
            f"credentials 只支持 headers / env 两层，收到未知层：{unknown_layers}。",
        )

    normalized: dict[str, dict[str, str]] = {}
    for layer in ("headers", "env"):
        values = credentials.get(layer)
        if values in (None, {}, []):
            continue
        if not isinstance(values, dict):
            raise _fail(_ERROR_CREDENTIALS_INVALID, f"credentials.{layer} 必须是「名称 → 引用」的对象。")
        layer_out: dict[str, str] = {}
        for name, ref in values.items():
            layer_out[str(name)] = _validate_credential_ref(ref, layer)
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


def _validate_credential_ref(ref: object, layer: str) -> str:
    if not isinstance(ref, str) or not ref.strip():
        raise _fail(_ERROR_CREDENTIALS_INVALID, f"credentials.{layer} 的值必须是非空字符串引用。")
    text = ref.strip()
    scheme, _, _rest = text.partition(":")
    if scheme not in ("env", "file", "keychain") or not text[len(scheme) + 1:].strip():
        # 没有已知前缀 ⇒ 这是要把明文塞进注册表。拒绝并给改写指引（设计稿 §4.2 A）。
        raise _fail(
            _ERROR_CREDENTIAL_REFUSED,
            f"注册表不接受明文凭证（{layer}.{text[:8]!r}…）。请改用环境变量引用："
            "先把密钥放进环境变量，再登记 \"env:变量名\"（如 \"env:GITHUB_TOKEN\"）。",
        )
    if scheme in ("file", "keychain"):
        raise _fail(
            _ERROR_CREDENTIAL_UNIMPLEMENTED,
            f"凭证引用 {scheme}: 首版未实现（只支持 env:），也不会降级成明文使用。"
            "请改用 env: 引用，或等 file:/keychain: 支持落地后再登记。",
        )
    return text


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
    """注册项的**脱敏**摘要（ls / rm / name_exists 附带说明共用同一份形态）。"""
    return {
        "name": entry.name,
        "kind": entry.kind,
        "target_masked": mask_target(entry.target),
        "note": entry.note,
        "enable": bool(entry.enable),
        "has_credentials": bool(entry.credentials),
        "updated_at": str(entry.updated_at) if entry.updated_at else None,
    }


def credential_refs_of(entry: GtMcpServer) -> dict:
    """从注册项取出凭证引用（**原样引用，不解析**——1331 硬条件 ⅰ）。"""
    credentials = entry.credentials or {}
    return {
        "headers": dict(credentials.get("headers") or {}),
        "env": dict(credentials.get("env") or {}),
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
    credentials: dict | None = None,
    overwrite: bool = False,
    created_by: int = 0,
) -> GtMcpServer:
    """登记 / 显式覆盖一个注册项（全局，无 team 作用域）。insert-only 是默认路径。

    正确性不依赖「先查后插」：并发第二写由全局唯一索引 `ux_mcp_servers_name` 兜底，
    `IntegrityError` 统一转 `mcp_name_exists`（与预检语义一致）。`overwrite` 只覆盖
    kind/target/note/credentials，**不动 enable**（禁用状态属运维态，覆盖登记不改它）。
    """
    name_n = validate_name(name)
    kind_n = validate_kind(kind)
    target_n = validate_target(target)
    credentials_n = validate_credentials(credentials, kind_n)

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
        existing.credentials = credentials_n
        await gtMcpServerManager.update_entry(
            existing, ["kind", "target", "note", "credentials"]
        )
        logger.info("mcp registry: name %s 已覆盖", name_n)
        return existing

    entry = GtMcpServer(
        name=name_n,
        kind=kind_n,
        target=target_n,
        note=note or "",
        credentials=credentials_n,
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
