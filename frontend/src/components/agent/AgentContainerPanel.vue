<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import type { AgentDetail } from '../../types';

const props = defineProps<{
  initialAgent: AgentDetail | null;
}>();

const { t } = useI18n();

/**
 * 当前所处的执行环境（宿主 / 容器）。
 *
 * 单独成 Tab 后这份数据仍取自 Agent 详情接口的快照；实时变化走
 * `agent_container` 事件推进 runtimeStore，再由列表徽标反映，此处不重复订阅。
 */
const execEnvLabel = computed(() => (
  props.initialAgent?.exec_env === 'container' ? t('agent.execEnv.container') : t('agent.execEnv.host')
));

const currentContainerId = computed(() => props.initialAgent?.current_container_id ?? null);

const containers = computed(() => props.initialAgent?.containers ?? []);

/** 徽标数字优先用接口的 container_count；缺失时退回列表长度，避免显示 0 而实际有行。 */
const containerCount = computed(() => {
  const fromApi = props.initialAgent?.container_count;
  if (typeof fromApi === 'number') {
    return fromApi;
  }
  return containers.value.length;
});
</script>

<template>
  <div class="agent-container-panel">
    <div class="form-section">
      <h3 class="section-title">
        {{ t('agent.execEnv.sectionTitle') }}
      </h3>
      <div class="config-mode-row">
        <span class="row-label">{{ t('agent.execEnv.label') }}</span>
        <span class="env-value">{{ execEnvLabel }}</span>
      </div>
      <div v-if="currentContainerId" class="config-mode-row">
        <span class="row-label">{{ t('agent.execEnv.currentContainer') }}</span>
        <span class="env-value mono">{{ currentContainerId }}</span>
      </div>
    </div>

    <div class="form-section">
      <h3 class="section-title">
        {{ t('agent.containers.label') }}
        <span v-if="containerCount > 0" class="count-hint">{{ containerCount }}</span>
      </h3>
      <p v-if="!containers.length" class="empty-hint">{{ t('agent.containers.empty') }}</p>
      <table v-else class="container-table">
        <thead>
          <tr>
            <th>{{ t('agent.containers.name') }}</th>
            <th>{{ t('agent.containers.containerId') }}</th>
            <th>{{ t('agent.containers.image') }}</th>
            <th>{{ t('agent.containers.state') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in containers" :key="item.record_id">
            <td>
              {{ item.container_name }}
              <span v-if="item.is_entered" class="entered-badge">{{ t('agent.containers.entered') }}</span>
            </td>
            <td class="mono">
              {{ item.container_id || t('agent.containers.pending') }}
            </td>
            <td class="mono">{{ item.image }}</td>
            <!-- Docker 原始状态直接展示，不做翻译（文档 §8 明确不加 containerState.* 词条）。 -->
            <td class="mono">{{ item.state }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.agent-container-panel {
  padding: 16px;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
}

.form-section {
  display: flex;
  flex-direction: column;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--text-primary);
  display: flex;
  align-items: center;
}

.config-mode-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.row-label {
  font-size: 13px;
  color: var(--text-secondary);
  width: 72px;
  flex-shrink: 0;
}

/* ─── 当前环境 / 容器表 ───────────────────────────────────────────── */
.env-value {
  font-size: 13px;
  color: var(--text-primary);
}

.env-value.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--text-secondary);
}

.count-hint {
  margin-left: 8px;
  padding: 0 6px;
  border-radius: 6px;
  background: color-mix(in srgb, var(--state-info) 14%, var(--surface-panel) 86%);
  color: color-mix(in srgb, var(--state-info) 84%, var(--text-primary) 16%);
  font-size: 0.7rem;
  font-weight: 500;
}

.empty-hint {
  margin: 0;
  font-size: 13px;
  color: var(--text-tertiary);
}

.container-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.container-table th,
.container-table td {
  padding: 6px 8px;
  text-align: left;
  border-bottom: 1px solid var(--border-default);
  vertical-align: top;
}

.container-table th {
  color: var(--text-secondary);
  font-weight: 500;
  white-space: nowrap;
}

.container-table td {
  color: var(--text-primary);
  word-break: break-all;
}

.container-table .mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: var(--text-secondary);
}

.entered-badge {
  margin-left: 6px;
  padding: 0 5px;
  border-radius: 5px;
  background: color-mix(in srgb, var(--state-success) 14%, var(--surface-panel) 86%);
  color: color-mix(in srgb, var(--state-success) 84%, var(--text-primary) 16%);
  font-size: 0.66rem;
  white-space: nowrap;
}
</style>
