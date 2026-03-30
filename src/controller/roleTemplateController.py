from controller.baseController import BaseHandler
from constants import DriverType, RoleTemplateType
from dal.db import gtRoleTemplateManager
from model.dbModel.gtAgent import GtAgent
from model.dbModel.gtRoleTemplate import GtRoleTemplate
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
    name: str
    soul: str = ""
    model: str | None = None
    driver: DriverType | None = None
    allowed_tools: list[str] | None = None


def _serialize_role_template(definition) -> dict:
    return {
        "id": definition.id,
        "name": definition.template_name,
        "model": definition.model or "",
        "prompt": definition.soul,
        "type": definition.type.value if definition.type else None,
        "driver": definition.driver.value if definition.driver else None,
        "allowed_tools": definition.allowed_tools,
    }


class RoleTemplateListHandler(BaseHandler):
    """GET /role_templates/list.json - 获取所有 role templates"""

    async def get(self) -> None:
        templates = await gtRoleTemplateManager.get_all_role_templates()
        self.return_json({"role_templates": [_serialize_role_template(t) for t in templates]})


class RoleTemplateCreateHandler(BaseHandler):
    """POST /role_templates/create.json - 创建用户自定义 role template"""

    async def post(self) -> None:
        request = self.parse_request(CreateRoleTemplateRequest)

        existing = await gtRoleTemplateManager.get_role_template_by_name(request.name)
        assertUtil.assertEqual(
            existing,
            None,
            error_message=f"Role template '{request.name}' already exists",
            error_code="role_template_exists",
        )

        created = await gtRoleTemplateManager.save_role_template(
            GtRoleTemplate(
                template_name=request.name,
                model=request.model,
                soul=request.soul,
                type=RoleTemplateType.USER,
                driver=request.driver,
                allowed_tools=request.allowed_tools,
            )
        )

        self.return_json(_serialize_role_template(created))


class RoleTemplateDetailHandler(BaseHandler):
    async def get(self, template_id: str) -> None:
        definition = await gtRoleTemplateManager.get_role_template_by_id(int(template_id))
        assertUtil.assertNotNull(
            definition,
            error_message=f"Role template '{template_id}' not found",
            error_code="role_template_not_found",
        )

        self.return_json(_serialize_role_template(definition))


class RoleTemplateModifyHandler(BaseHandler):
    """POST /role_templates/{id}/modify.json - 修改 role template"""

    async def post(self, template_id: str) -> None:
        definition = await gtRoleTemplateManager.get_role_template_by_id(int(template_id))
        assertUtil.assertNotNull(
            definition,
            error_message=f"Role template '{template_id}' not found",
            error_code="role_template_not_found",
        )

        request = self.parse_request(ModifyRoleTemplateRequest)

        next_name = request.name.strip()
        assertUtil.assertTrue(
            len(next_name) > 0,
            error_message="Role template name must not be empty",
            error_code="role_template_name_empty",
        )
        if next_name != definition.template_name:
            existing = await gtRoleTemplateManager.get_role_template_by_name(next_name)
            assertUtil.assertEqual(
                existing,
                None,
                error_message=f"Role template '{next_name}' already exists",
                error_code="role_template_exists",
            )

        definition.template_name = next_name
        definition.soul = request.soul
        definition.model = request.model
        definition.driver = request.driver
        definition.allowed_tools = request.allowed_tools

        updated = await gtRoleTemplateManager.save_role_template(definition)

        self.return_json(_serialize_role_template(updated))


class RoleTemplateDeleteHandler(BaseHandler):
    """POST /role_templates/{id}/delete.json - 删除 role template"""

    async def post(self, template_id: str) -> None:
        definition = await gtRoleTemplateManager.get_role_template_by_id(int(template_id))
        assertUtil.assertNotNull(
            definition,
            error_message=f"Role template '{template_id}' not found",
            error_code="role_template_not_found",
        )
        assertUtil.assertEqual(
            definition.type,
            RoleTemplateType.USER,
            error_message="系统模板不允许删除",
            error_code="role_template_delete_forbidden",
        )

        referenced_agents = await GtAgent.aio_get_or_none(
            GtAgent.role_template_id == int(template_id)
        )
        assertUtil.assertEqual(
            referenced_agents,
            None,
            error_message=f"Role template '{definition.template_name}' is in use",
            error_code="role_template_in_use",
        )

        await gtRoleTemplateManager.delete_role_template(int(template_id))
        self.return_json({"status": "deleted", "id": definition.id, "name": definition.template_name})
