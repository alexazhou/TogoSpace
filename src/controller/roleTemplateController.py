import service.roleTemplateService as roleTemplateService
from controller.baseController import BaseHandler
from util import assertUtil, configUtil


class RoleTemplateListHandler(BaseHandler):
    """GET /role_templates/list.json - 获取所有 role templates"""

    async def get(self) -> None:
        templates = roleTemplateService.get_all_role_templates()
        data = [
            {
                "name": t.name,
                "model": t.model or "",
            }
            for t in templates
        ]
        self.return_json({"role_templates": data})


class RoleTemplateDetailHandler(BaseHandler):
    async def get(self, template_name: str) -> None:
        definition = roleTemplateService.get_role_template(template_name)
        assertUtil.assertNotNull(
            definition,
            error_message=f"Role template '{template_name}' not found",
            error_code="role_template_not_found",
        )
        if definition is None:
            return

        if definition.system_prompt:
            prompt = definition.system_prompt
        else:
            prompt = configUtil.load_prompt(definition.prompt_file)

        self.return_json(
            {
                "name": definition.name,
                "model": definition.model or "",
                "prompt": prompt,
            }
        )
