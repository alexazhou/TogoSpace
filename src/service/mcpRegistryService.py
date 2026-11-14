"""team 级 MCP server 注册表 service（#224，按 #223 设计稿 §2.4）。

分层边界（设计稿 §1）：命令面只转发；别名解析在 `tools.mcp_call` 入口（那里有
`_context`→team_id）；**业务规则在本层**；工具薄壳只做上下文提取、参数转发、异常转
`toolResult`。

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

#: 别名格式（设计稿 §3.2）：小写字母开头，`[a-z0-9_-]`，≤32 字符。依据是解析器边界
#: （shlex / `--` 前缀当选项 / `-h` 全局短路），不是偏好。
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

#: kind 合法值（`tools.mcp_call` 同一口径，`mcpTool._SOURCE_FLAGS` 的键集）。
VALID_KINDS = ("openapi", "mcp", "mcp_stdio", "graphql")

#: HTTP 类 kind：headers 凭证（`--auth-header`）只对它们有意义。
_HTTP_KINDS = ("openapi", "mcp", "graphql")
#: stdio 类 kind：env 凭证（`--env`，注入子进程环境）只对它有意义。
_STDIO_KIND = "mcp_stdio"

_ERROR_ALIAS_INVALID = "mcp_invalid_alias"
_ERROR_KIND_INVALID = "mcp_invalid_kind"
_ERROR_TARGET_INVALID = "mcp_invalid_target"
_ERROR_ALIAS_EXISTS = "mcp_alias_exists"
_ERROR_ALIAS_NOT_FOUND = "mcp_alias_not_found"
_ERROR_CREDENTIALS_INVALID = "mcp_invalid_credentials"
_ERROR_CREDENTIAL_REFUSED = "mcp_inline_credential_refused"
_ERROR_CREDENTIAL_UNIMPLEMENTED = "mcp_credential_ref_unimplemented"
_ERROR_CREDENTIAL_REF_INVALID = "mcp_invalid_credential_ref"
_ERROR_CREDENTIAL_KIND_MISMATCH = "mcp_credential_kind_mismatch"


def _fail(error_code: str, message: str) -> TogoException:
    return TogoException(message, error_code=error_code)


def validate_alias(alias: str) -> str:
    """别名格式校验；通过返回 strip 后的值，不通过抛 `mcp_invalid_alias`。"""
    text = str(alias or "").strip()
    if not _ALIAS_RE.match(text):
        raise _fail(
            _ERROR_ALIAS_INVALID,
            "别名不合法：须为小写字母开头、仅含小写字母/数字/下划线/连字符、"
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


async def resolve_alias(team_id: int, name: str) -> GtMcpServer | None:
    """按传入值精确查 `alias` 列；命中返回注册项，未命中返回 None（透传语义，设计稿 §3.6）。

    `mcp call` 与 `mcp ls <别名>` 两个解析点**必须**复用本函数（C3：同一语义两处实现
    必然漂移）。
    """
    text = str(name or "").strip()
    if not text:
        return None
    return await gtMcpServerManager.get_by_alias(team_id, text)


def entry_summary(entry: GtMcpServer) -> dict:
    """注册项的**脱敏**摘要（ls / rm / alias_exists 附带说明共用同一份形态）。"""
    return {
        "alias": entry.alias,
        "kind": entry.kind,
        "target_masked": mask_target(entry.target),
        "note": entry.note,
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
    team_id: int,
    alias: str,
    kind: str = "mcp",
    target: str,
    note: str = "",
    credentials: dict | None = None,
    overwrite: bool = False,
    created_by: int = 0,
) -> GtMcpServer:
    """登记 / 显式覆盖一个注册项。insert-only 是默认路径（设计稿 §5.3-③）。

    正确性不依赖「先查后插」：并发第二写由 `(team_id, alias)` 唯一索引兜底，
    `IntegrityError` 统一转 `mcp_alias_exists`（与预检语义一致）。
    """
    alias_n = validate_alias(alias)
    kind_n = validate_kind(kind)
    target_n = validate_target(target)
    credentials_n = validate_credentials(credentials, kind_n)

    existing = await gtMcpServerManager.get_by_alias(team_id, alias_n)
    if existing is not None and not overwrite:
        raise _fail(
            _ERROR_ALIAS_EXISTS,
            f"别名 {alias_n!r} 已登记（{entry_summary(existing)}）。"
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
        logger.info("mcp registry: 别名 %s 已覆盖（team=%s）", alias_n, team_id)
        return existing

    entry = GtMcpServer(
        team_id=team_id,
        alias=alias_n,
        kind=kind_n,
        target=target_n,
        note=note or "",
        credentials=credentials_n,
        created_by=created_by,
    )
    try:
        await gtMcpServerManager.insert_entry(entry)
    except peewee.IntegrityError as exc:
        # 唯一索引冲突统一转业务错误码（§5.3-⑥）：并发第二写与预检语义一致。
        raise _fail(
            _ERROR_ALIAS_EXISTS,
            f"别名 {alias_n!r} 已登记（并发写入撞唯一索引）。覆盖须显式 overwrite=true。",
        ) from exc
    logger.info("mcp registry: 别名 %s 已登记（team=%s，kind=%s）", alias_n, team_id, kind_n)
    return entry


async def delete_server(team_id: int, alias: str) -> GtMcpServer:
    """只按别名精确删（不支持按 target / 前缀）；不存在抛 `mcp_alias_not_found`。"""
    alias_n = validate_alias(alias)
    existing = await gtMcpServerManager.get_by_alias(team_id, alias_n)
    if existing is None:
        raise _fail(
            _ERROR_ALIAS_NOT_FOUND,
            f"注册表里没有别名 {alias_n!r}（本 team）。mcp ls 可先看现有登记项。",
        )
    await gtMcpServerManager.delete_by_alias(team_id, alias_n)
    logger.info("mcp registry: 别名 %s 已删除（team=%s）", alias_n, team_id)
    return existing


async def list_servers(team_id: int) -> list[GtMcpServer]:
    return await gtMcpServerManager.list_by_team(team_id)
