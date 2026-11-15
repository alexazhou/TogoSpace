"""全局 MCP server 注册表条目（#273，Operator 2026-11-15 17:54 需求变更）。

表 `mcp_servers`，唯一索引 `(name)`（全局唯一）—— 并发写的兜底是**数据库级唯一约束**，
不是 service 层的「先查后插」（那是 TOCTOU 窗口，设计稿 §5.3 明确禁止只靠它）。

注册表**不分 team**（Operator：「mcp 录入之后，不需要分团队，先设计成全局的」）；
`created_by` 仅审计。`enable=0` 的条目禁用但保留（ls 仍可见），工具列表不出现。

`config` **原样存值**（#277，Operator 2026-11-15 20:21 需求变更，覆盖 #273 的
「只存引用」）：明文直接入库，无前缀校验。脱敏全部改为**展示面**：`to_json()`、
`ls` 摘要、日志与异常文案在输出前打码（保留少量前缀便于识别、主体 `***`）。
`created_by` 仅审计。

## 脱敏为什么放在模型层

`mask_target` / `mask_config_for_display` 是**数据的展示属性**（设计稿 §4.5），
不是业务规则：`to_json()` 要用、服务层的 ls/rm 返回体也要用。放 service 会造成
model→service 的反向依赖（import 圈），放这里两边都向上引用，无圈。
"""
from __future__ import annotations

import re

import peewee

from .base import DbModelBase, JsonField

#: 「形如长 token 的段」（设计稿 §4.5-2）：连续 token 字符 ≥ 32 位即视为疑似内嵌凭证。
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{32,}")

#: 凭证值展示时保留的识别前缀上限（#277：少量前缀便于识别、主体打码）。
_CREDENTIAL_VISIBLE_PREFIX = 8


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


def mask_config_value(value: str) -> str:
    """单个凭证值的展示形态（#277）：保留识别前缀 + `***`（值已明文存储）。

    识别前缀取第一个空格/冒号前的 token（如 `Bearer`、`sk-TESTO`），上限 8 字符；
    值不比前缀长（短值）整段打码，不露任何完整值。
    """
    text = str(value or "")
    if not text:
        return "***"
    token = re.split(r"[\s:]", text, maxsplit=1)[0]
    prefix = token[:_CREDENTIAL_VISIBLE_PREFIX]
    if len(text) <= len(prefix):
        return "***"
    return f"{prefix}***"


def mask_config_for_display(config: dict) -> dict:
    """两层凭证结构的展示形态：逐值过 `mask_config_value`，空凭证统一 `{}`。"""
    if not config:
        return {}
    masked: dict[str, dict[str, str]] = {}
    for layer in ("headers", "env"):
        values = config.get(layer)
        if isinstance(values, dict) and values:
            masked[layer] = {str(k): mask_config_value(v) for k, v in values.items()}
    return masked


class GtMcpServer(DbModelBase):
    name:        str   = peewee.CharField()                # 全局唯一（ux_mcp_servers_name）；原 #224 半成品的 alias（#273 改名）
    kind:        str   = peewee.CharField(default="mcp")   # openapi/mcp/mcp_stdio/graphql
    target:      str   = peewee.TextField()
    note:        str   = peewee.TextField(default="")
    # #279（Operator 2026-11-16 00:25 拍板「方案2」）：0023 文件直接改为 `config` 列，
    # 真库 drop 表 + 删账本 id=23 重跑（清理由 PM 执行）；模型字段与列同名。
    # 内容仍是 {"headers":{...}}/"env" 两个 dict；#277 起原样存值（明文）。
    config: dict  = JsonField(default=dict)             # {"headers":{名:值},"env":{变量:值}}；#277 起原样存值（明文）
    enable:      int   = peewee.IntegerField(default=1)    # 1=启用；0=禁用（禁用≠删除，ls 仍可见；#273 新增）
    created_by:  int   = peewee.IntegerField(default=0)    # 登记的 agent_id，仅审计（注册表全局共享，不做隔离）

    class Meta:
        table_name = "mcp_servers"
        indexes = ((("name",), True),)

    def to_json(self) -> dict:
        """脱敏序列化：任何拿到模型对象转 JSON 的路径都不泄**完整**值（保留前缀掩码）。"""
        data = super().to_json()
        data["target_masked"] = mask_target(str(data.get("target") or ""))
        data.pop("target", None)
        data["config"] = mask_config_for_display(data.get("config") or {})
        return data


__all__ = [
    "GtMcpServer",
    "mask_target",
    "mask_config_for_display",
    "mask_config_value",
]
