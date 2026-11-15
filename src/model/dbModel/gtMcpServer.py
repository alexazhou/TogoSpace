"""全局 MCP server 注册表条目（#273，Operator 2026-11-15 17:54 需求变更）。

表 `mcp_servers`，唯一索引 `(name)`（全局唯一）—— 并发写的兜底是**数据库级唯一约束**，
不是 service 层的「先查后插」（那是 TOCTOU 窗口，设计稿 §5.3 明确禁止只靠它）。

注册表**不分 team**（Operator：「mcp 录入之后，不需要分团队，先设计成全局的」）；
`created_by` 仅审计。`enable=0` 的条目禁用但保留（ls 仍可见），工具列表不出现。

`credentials` 只存**引用**（`env:VAR` 形态），永不存明文（设计稿 §4：明文入 DB 不成立，
日志回显与 Agent 主动读两条路径都与部署形态无关）。`created_by` 仅审计。

## 脱敏为什么放在模型层

`mask_target` / `mask_credentials_for_display` 是**数据的展示属性**（设计稿 §4.5），
不是业务规则：`to_json()` 要用、服务层的 ls/rm 返回体也要用。放 service 会造成
model→service 的反向依赖（import 圈），放这里两边都向上引用，无圈。
"""
from __future__ import annotations

import re

import peewee

from .base import DbModelBase, JsonField

#: 「形如长 token 的段」（设计稿 §4.5-2）：连续 token 字符 ≥ 32 位即视为疑似内嵌凭证。
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{32,}")

#: 引用前缀白名单（设计稿 §4.3 B1）。首版只实现 `env:`；`file:`/`keychain:` 校验在
#: service 层报「未实现」，这里只负责展示面打码。
_CREDENTIAL_REF_PREFIXES = ("env:", "file:", "keychain:")


def mask_target(target: str) -> str:
    """target 的展示形态：`?` 之后的整段、以及长 token 段替换为 `***`。

    URL query 是最常见的内嵌凭证位（`?api_key=…`），整段打码不做例外枚举——
    例外清单永远追不全新形态。
    """
    text = str(target or "")
    head, _, _query = text.partition("?")
    masked = _LONG_TOKEN_RE.sub("***", head)
    if _query:
        masked = f"{masked}?***"
    return masked


def mask_credential_value(value: str) -> str:
    """单个凭证值的展示形态（设计稿 §4.5-1）：`env:VAR` 原样露引用；其余打码。"""
    text = str(value or "")
    if text.startswith("env:"):
        return text  # 引用原文允许展示
    if text.startswith("file:") or text.startswith("keychain:"):
        return f"{text.split(':', 1)[0]}:***"
    return f"{text.split(':', 1)[0]}:***" if ":" in text else "***"


def mask_credentials_for_display(credentials: dict) -> dict:
    """两层凭证结构的展示形态：逐值过 `mask_credential_value`，空凭证统一 `{}`。"""
    if not credentials:
        return {}
    masked: dict[str, dict[str, str]] = {}
    for layer in ("headers", "env"):
        values = credentials.get(layer)
        if isinstance(values, dict) and values:
            masked[layer] = {str(k): mask_credential_value(v) for k, v in values.items()}
    return masked


class GtMcpServer(DbModelBase):
    name:        str   = peewee.CharField()                # 全局唯一（ux_mcp_servers_name）；原 #224 半成品的 alias（#273 改名）
    kind:        str   = peewee.CharField(default="mcp")   # openapi/mcp/mcp_stdio/graphql
    target:      str   = peewee.TextField()
    note:        str   = peewee.TextField(default="")
    credentials: dict  = JsonField(default=dict)           # {"headers":{名:引用},"env":{变量:引用}}；空凭证统一 {}
    enable:      int   = peewee.IntegerField(default=1)    # 1=启用；0=禁用（禁用≠删除，ls 仍可见；#273 新增）
    created_by:  int   = peewee.IntegerField(default=0)    # 登记的 agent_id，仅审计（注册表全局共享，不做隔离）

    class Meta:
        table_name = "mcp_servers"
        indexes = ((("name",), True),)

    def to_json(self) -> dict:
        """脱敏序列化（设计稿 §4.5）：任何拿到模型对象转 JSON 的路径都不泄值。"""
        data = super().to_json()
        data["target_masked"] = mask_target(str(data.get("target") or ""))
        data.pop("target", None)
        data["credentials"] = mask_credentials_for_display(data.get("credentials") or {})
        return data


__all__ = [
    "GtMcpServer",
    "mask_target",
    "mask_credentials_for_display",
    "mask_credential_value",
]
