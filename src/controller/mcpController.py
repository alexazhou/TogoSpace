"""MCP 注册表 Web 管理后台（#277）。

边界：Controller 只做**参数校验 + 调 `mcpRegistryService`**，不复制业务规则
（name 格式 / kind / target / 凭证结构 / 全局唯一 / 启停语义都只在 service 一处，
同语义两处实现必然漂移）。业务错误由 `mcpRegistryService` 抛 `TogoException`，
`baseController.write_error` 统一转 400 + `error_code`/`error_desc`。

权限：**不限制**（#277 需求：任何使用者都能填写、查看，含完整凭证值）；鉴权仍由
`BaseHandler._check_auth` 统一处理（桌面 Token），本层不加额外门槛。

脱敏口径（#277）：**REST 响应给完整值**（Web 配置页要能回显、可编辑）；
明文不进日志的判据由 `mcpTool._mask_deep`（调用出口）与
`mask_credentials_for_display`（模型/ls 展示面）分别承担，本层不重复实现。
"""
from __future__ import annotations

from pydantic import BaseModel, ValidationError

from controller.baseController import BaseHandler
from service import mcpRegistryService


class McpServerSaveRequest(BaseModel):
    """POST /mcp/servers.json 请求体（name/kind/target 必填，凭证可选）。"""

    name: str
    kind: str = "mcp"
    target: str
    note: str = ""
    credentials: dict | None = None
    overwrite: bool = False


class McpServerNameRequest(BaseModel):
    """delete / enable 请求体：都只按 name 定位。"""

    name: str


class McpServerEnableRequest(McpServerNameRequest):
    enable: bool = True


def _entry_full(entry) -> dict:
    """注册项 → Web 响应体：**含完整 target 与凭证值**（Web 配置页要能回显/编辑）。"""
    return {
        "id": entry.id,
        "name": entry.name,
        "kind": entry.kind,
        "target": entry.target,
        "note": entry.note,
        "credentials": entry.credentials or {},
        "enable": bool(entry.enable),
        "created_by": entry.created_by,
        "updated_at": str(entry.updated_at) if entry.updated_at else None,
    }


class McpServerListHandler(BaseHandler):
    """GET /mcp/servers.json —— 列全部注册项（含完整凭证值，权限不限制）。"""

    async def get(self) -> None:
        entries = await mcpRegistryService.list_servers()
        self.return_json({"servers": [_entry_full(e) for e in entries], "count": len(entries)})


class McpServerSaveHandler(BaseHandler):
    """POST /mcp/servers.json —— 新增 / 覆盖登记（业务校验全在 service）。"""

    async def post(self) -> None:
        try:
            request = self.parse_request(McpServerSaveRequest)
        except ValidationError as exc:
            self.return_with_error(error_code="mcp_request_invalid", error_desc=str(exc.errors()[0].get("msg")))
            return
        entry = await mcpRegistryService.save_server(
            name=request.name,
            kind=request.kind,
            target=request.target,
            note=request.note,
            credentials=request.credentials,
            overwrite=request.overwrite,
            created_by=0,  # Web 后台无 agent 上下文，审计字段记 0
        )
        self.return_json(_entry_full(entry))


class McpServerDeleteHandler(BaseHandler):
    """POST /mcp/servers/delete.json —— 按 name 删除（不存在由 service 报 mcp_name_not_found）。"""

    async def post(self) -> None:
        try:
            request = self.parse_request(McpServerNameRequest)
        except ValidationError as exc:
            self.return_with_error(error_code="mcp_request_invalid", error_desc=str(exc.errors()[0].get("msg")))
            return
        removed = await mcpRegistryService.delete_server(request.name)
        self.return_json({"deleted": mcpRegistryService.entry_summary(removed)})


class McpServerEnableHandler(BaseHandler):
    """POST /mcp/servers/enable.json —— 启用/禁用（禁用≠删除，ls 仍可见）。"""

    async def post(self) -> None:
        try:
            request = self.parse_request(McpServerEnableRequest)
        except ValidationError as exc:
            self.return_with_error(error_code="mcp_request_invalid", error_desc=str(exc.errors()[0].get("msg")))
            return
        entry = await mcpRegistryService.set_enabled(request.name, request.enable)
        self.return_json(_entry_full(entry))
