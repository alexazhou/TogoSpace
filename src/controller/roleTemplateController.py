import service.roleTemplateService as roleTemplateService
from controller.baseController import BaseHandler
from util import assertUtil, configUtil


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
