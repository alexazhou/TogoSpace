from controller.baseController import BaseHandler
from constants import DriverType, RoleTemplateType
from dal.db import gtRoleTemplateManager
from pydantic import BaseModel
from util import assertUtil


class CreateRoleTemplateRequest(BaseModel):
    name: str
    soul: str = ""
    model: str | None = None
    driver: DriverType | None = None
    allowed_tools: list[str] | None = None


class ModifyRoleTemplateRequest(BaseModel):
    """修改 role template 的请求体。"""
    soul: str | None = None
    model: str | None = None
    driver: DriverType | None = None
    allowed_tools: list[str] | None = None


class RoleTemplateListHandler(BaseHandler):
    """GET /role_templates/list.json - 获取所有 role templates"""

    async def get(self) -> None:
        templates = await gtRoleTemplateManager.get_all_role_templates()
        data = [
            {
                "name": t.template_name,
                "model": t.model or "",
                "type": t.type.value if t.type else None,
                "driver": t.driver.value if t.driver else None,
            }
            for t in templates
        ]
        self.return_json({"role_templates": data})


class RoleTemplateCreateHandler(BaseHandler):
    """POST /role_templates/create.json - 创建用户自定义 role template"""

    async def post(self) -> None:
        request = self.parse_request(CreateRoleTemplateRequest)

        existing = await gtRoleTemplateManager.get_role_template(request.name)
        assertUtil.assertEqual(
            existing,
            None,
            error_message=f"Role template '{request.name}' already exists",
            error_code="role_template_exists",
        )

        created = await gtRoleTemplateManager.upsert_role_template(
            request.name,
            request.model,
            request.soul,
            RoleTemplateType.USER,
            request.driver,
            request.allowed_tools,
        )

        self.return_json(
            {
                "name": created.template_name,
                "model": created.model or "",
                "prompt": created.soul,
                "type": created.type.value if created.type else None,
                "driver": created.driver.value if created.driver else None,
                "allowed_tools": created.allowed_tools,
            }
        )


class RoleTemplateDetailHandler(BaseHandler):
    async def get(self, template_name: str) -> None:
        definition = await gtRoleTemplateManager.get_role_template(template_name)
        assertUtil.assertNotNull(
            definition,
            error_message=f"Role template '{template_name}' not found",
            error_code="role_template_not_found",
        )

        self.return_json(
            {
                "name": definition.template_name,
                "model": definition.model or "",
                "prompt": definition.soul,
                "type": definition.type.value if definition.type else None,
                "driver": definition.driver.value if definition.driver else None,
                "allowed_tools": definition.allowed_tools,
            }
        )


class RoleTemplateModifyHandler(BaseHandler):
    """POST /role_templates/{name}/modify.json - 修改 role template"""

    async def post(self, template_name: str) -> None:
        definition = await gtRoleTemplateManager.get_role_template(template_name)
        assertUtil.assertNotNull(
            definition,
            error_message=f"Role template '{template_name}' not found",
            error_code="role_template_not_found",
        )

        request = self.parse_request(ModifyRoleTemplateRequest)

        updated = await gtRoleTemplateManager.update_role_template(
            template_name,
            soul=request.soul,
            model=request.model,
            driver=request.driver,
            allowed_tools=request.allowed_tools,
        )

        self.return_json(
            {
                "name": updated.template_name,
                "model": updated.model or "",
                "prompt": updated.soul,
                "type": updated.type.value if updated.type else None,
                "driver": updated.driver.value if updated.driver else None,
                "allowed_tools": updated.allowed_tools,
            }
        )
