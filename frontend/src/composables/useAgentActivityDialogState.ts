import { computed, ref, type Ref } from 'vue';
import { useI18n } from 'vue-i18n';
import type { AgentInfo, AgentStatus, RoleTemplateSummary } from '../types';
import { displayName } from '../utils';

export function useAgentActivityDialogState(
  agents: Ref<AgentInfo[]>,
  roleTemplates: Ref<RoleTemplateSummary[]>,
) {
  const { t } = useI18n();
  const open = ref(false);
  const selectedAgentId = ref<number | null>(null);

  const roleTemplateNameMap = computed(
    () => new Map(roleTemplates.value.map((template) => [template.id, displayName(template)])),
  );

  const selectedAgentName = computed<string | null>(() => {
    const agent = agents.value.find((item) => item.id === selectedAgentId.value);
    return agent ? displayName(agent) : null;
  });

  const selectedAgentStatus = computed<AgentStatus | null>(
    () => agents.value.find((agent) => agent.id === selectedAgentId.value)?.status ?? null,
  );

  const selectedAgentTemplateName = computed<string | null>(() => {
    const roleTemplateId = agents.value.find((agent) => agent.id === selectedAgentId.value)?.role_template_id;
    if (typeof roleTemplateId !== 'number') {
      return null;
    }

    return roleTemplateNameMap.value.get(roleTemplateId) ?? t('agent.templateFallback', { id: roleTemplateId });
  });

  function openAgent(agentId: number): void {
    selectedAgentId.value = agentId;
    open.value = true;
  }

  function closeAgentDetail(): void {
    open.value = false;
    selectedAgentId.value = null;
  }

  return {
    open,
    selectedAgentId,
    selectedAgentName,
    selectedAgentStatus,
    selectedAgentTemplateName,
    openAgent,
    closeAgentDetail,
  };
}
