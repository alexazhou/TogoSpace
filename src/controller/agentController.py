import service.agentService as agentService
from controller.baseController import BaseHandler
from util import assertUtil, configUtil


class AgentDetailHandler(BaseHandler):
    async def get(self, agent_name: str) -> None:
        definition = agentService.get_agent_definition(agent_name)
        assertUtil.assertNotNull(
            definition,
            error_message=f"Agent template '{agent_name}' not found",
            error_code="agent_not_found",
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
