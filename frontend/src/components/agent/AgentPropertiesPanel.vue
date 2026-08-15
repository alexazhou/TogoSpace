<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { getAvailableSkills, getAvailableTools, getFrontendConfig, updateAgentProperties, SkillConfig, ToolConfig } from '../../api';
import { showGlobalSuccessToast } from '../../appUiState';
import CustomMultiSelect from '../ui/CustomMultiSelect.vue';
import CustomSelect from '../ui/CustomSelect.vue';
import type { AgentDetail, ModelSlotOption } from '../../types';
import { MODEL_SLOT_LABELS } from '../../types';

const props = defineProps<{
  agentId: number | null;
  initialAgent: AgentDetail | null;
}>();

const emit = defineEmits<{
  saved: [agent: AgentDetail];
}>();

const { t } = useI18n();

const loadingConfig = ref(false);
const saving = ref(false);

const availableTools = ref<ToolConfig[]>([]);
const availableSkills = ref<SkillConfig[]>([]);
const modelSlots = ref<ModelSlotOption[]>([]);

const toolOptions = computed(() => availableTools.value.map(t => ({
  value: t.name,
  label: t.name,
  category: t.category
})));

const skillOptions = computed(() => availableSkills.value.map(s => ({
  value: s.name,
  label: s.name,
  category: s.is_builtin ? t('agent.builtinSkill') : t('agent.userSkill'),
  categoryType: (s.is_builtin ? 'info' : 'success') as 'info' | 'success'
})));

const configModeOptions = computed(() => [
  { value: 'auto', label: t('common.auto') },
  { value: 'manual', label: t('common.manual') }
]);

const modelOptions = computed(() => [
  { value: '', label: t('common.auto') },
  ...modelSlots.value.map((slot) => ({
    value: `${slot.key}@system`,
    label: MODEL_SLOT_LABELS[slot.key] ?? slot.key,
  })),
]);

const configModeTools = ref('auto');
const configModeSkills = ref('auto');
const selectedModel = ref('');
const selectedTools = ref<string[]>([]);
const selectedSkills = ref<string[]>([]);

async function loadConfig() {
  loadingConfig.value = true;
  try {
    const [tools, skills, frontendConfig] = await Promise.all([
      getAvailableTools(),
      getAvailableSkills(),
      getFrontendConfig(),
    ]);
    availableTools.value = tools;
    availableSkills.value = skills;
    modelSlots.value = frontendConfig.model_slots;
  } catch (error) {
    console.error('Failed to load tools and skills config', error);
  } finally {
    loadingConfig.value = false;
  }
}

watch(
  () => props.initialAgent,
  (agent) => {
    if (agent) {
      selectedModel.value = agent.model || '';
      if (agent.allow_tools === null || agent.allow_tools === undefined) {
        configModeTools.value = 'auto';
        selectedTools.value = [];
      } else {
        configModeTools.value = 'manual';
        selectedTools.value = [...agent.allow_tools];
      }
      if (agent.allow_skills === null || agent.allow_skills === undefined) {
        configModeSkills.value = 'auto';
        selectedSkills.value = [];
      } else {
        configModeSkills.value = 'manual';
        selectedSkills.value = [...agent.allow_skills];
      }
    } else {
      selectedModel.value = '';
      configModeTools.value = 'auto';
      configModeSkills.value = 'auto';
      selectedTools.value = [];
      selectedSkills.value = [];
    }
  },
  { immediate: true }
);

onMounted(() => {
  loadConfig();
});

const canSave = computed(() => props.agentId !== null && !saving.value && hasChanges.value);

const hasChanges = computed(() => {
  if (!props.initialAgent) return false;

  if ((props.initialAgent.model || '') !== selectedModel.value) return true;

  const initialAutoTools = props.initialAgent.allow_tools === null || props.initialAgent.allow_tools === undefined;
  const isCurrentlyAuto = configModeTools.value === 'auto';

  if (initialAutoTools !== isCurrentlyAuto) return true;

  if (!isCurrentlyAuto) {
    const initialTools = [...(props.initialAgent.allow_tools || [])].sort();
    const currentTools = [...selectedTools.value].sort();
    if (initialTools.join(',') !== currentTools.join(',')) return true;
  }

  const initialAutoSkills = props.initialAgent.allow_skills === null || props.initialAgent.allow_skills === undefined;
  const isCurrentlyAutoSkills = configModeSkills.value === 'auto';

  if (initialAutoSkills !== isCurrentlyAutoSkills) return true;

  if (!isCurrentlyAutoSkills) {
    const initialSkills = [...(props.initialAgent.allow_skills || [])].sort();
    const currentSkills = [...selectedSkills.value].sort();
    if (initialSkills.join(',') !== currentSkills.join(',')) return true;
  }

  return false;
});

function handleRestore() {
  if (props.initialAgent) {
    selectedModel.value = props.initialAgent.model || '';
    if (props.initialAgent.allow_tools === null || props.initialAgent.allow_tools === undefined) {
      configModeTools.value = 'auto';
      selectedTools.value = [];
    } else {
      configModeTools.value = 'manual';
      selectedTools.value = [...props.initialAgent.allow_tools];
    }
    if (props.initialAgent.allow_skills === null || props.initialAgent.allow_skills === undefined) {
      configModeSkills.value = 'auto';
      selectedSkills.value = [];
    } else {
      configModeSkills.value = 'manual';
      selectedSkills.value = [...props.initialAgent.allow_skills];
    }
  }
}

async function handleSave() {
  if (!props.agentId) return;
  saving.value = true;
  try {
    const updatedAgent = await updateAgentProperties(props.agentId, {
      model: selectedModel.value,
      allow_tools: configModeTools.value === 'auto' ? null : selectedTools.value,
      allow_skills: configModeSkills.value === 'auto' ? null : selectedSkills.value,
    });
    showGlobalSuccessToast(t('agent.propertiesSaved'));
    emit('saved', updatedAgent);
  } catch (error) {
    console.error('Failed to save properties', error);
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <div class="agent-properties-panel">
    <div v-if="loadingConfig" class="loading-state">
      {{ t('common.loading') }}
    </div>
    <div v-else class="properties-form">
      <div class="form-section">
        <h3 class="section-title">
          {{ t('member.model') }}
        </h3>
        <div class="config-mode-row">
          <span class="row-label">{{ t('member.model') }}</span>
          <CustomSelect
            v-model="selectedModel"
            :options="modelOptions"
            :compact="true"
            style="width: 240px;"
          />
        </div>
      </div>

      <div class="form-section">
        <h3 class="section-title">
          {{ t('agent.allowTools') }}
          <span class="tooltip-wrapper">
            <span class="info-icon">ⓘ</span>
            <span class="tooltip-content">{{ t('agent.allowToolsDesc') }}</span>
          </span>
        </h3>
        
        <div class="config-mode-row">
          <span class="row-label">{{ t('common.configMode') }}</span>
          <CustomSelect
            v-model="configModeTools"
            :options="configModeOptions"
            :compact="true"
            style="width: 240px;"
          />
        </div>

        <div class="config-mode-row" v-if="configModeTools === 'manual'">
          <span class="row-label">{{ t('agent.toolList') }}</span>
          <div style="width: 240px;">
            <CustomMultiSelect
              v-model="selectedTools"
              :options="toolOptions"
              :placeholder="t('common.none')"
              :compact="true"
            />
          </div>
        </div>
      </div>

      <div class="form-section">
        <h3 class="section-title">
          {{ t('agent.skills') }}
          <span class="tooltip-wrapper">
            <span class="info-icon">ⓘ</span>
            <span class="tooltip-content">{{ t('agent.skillsDesc') }}</span>
          </span>
        </h3>
        <div class="config-mode-row">
          <span class="row-label">{{ t('common.configMode') }}</span>
          <CustomSelect
            v-model="configModeSkills"
            :options="configModeOptions"
            :compact="true"
            style="width: 240px;"
          />
        </div>

        <div class="config-mode-row" v-if="configModeSkills === 'manual'">
          <span class="row-label">{{ t('agent.skillList') }}</span>
          <div style="width: 240px;">
            <CustomMultiSelect
              v-model="selectedSkills"
              :options="skillOptions"
              :placeholder="t('common.none')"
              :compact="true"
            />
          </div>
        </div>
      </div>

      <div class="form-actions">
        <button
          v-if="hasChanges"
          type="button"
          class="secondary-button"
          :disabled="saving"
          @click="handleRestore"
          style="margin-right: 12px;"
        >
          {{ t('common.cancel') }}
        </button>
        <button
          type="button"
          class="primary-button save-button"
          :disabled="!canSave"
          @click="handleSave"
        >
          {{ saving ? t('common.saving') : t('agent.saveProperties') }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-properties-panel {
  padding: 16px;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}

.properties-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
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

.info-icon {
  display: inline-flex;
  margin-left: 6px;
  color: var(--text-secondary);
  font-size: 14px;
  font-weight: normal;
  cursor: help;
  align-items: center;
  justify-content: center;
}

.tooltip-wrapper {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.tooltip-content {
  position: absolute;
  top: 50%;
  left: calc(100% + 4px);
  transform: translateY(-50%);
  padding: 6px 12px;
  background: var(--surface-panel, #333);
  color: var(--text-primary, #fff);
  border: 1px solid var(--border-strong, #555);
  font-size: 12px;
  font-weight: normal;
  border-radius: 6px;
  white-space: nowrap;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.15s ease, visibility 0.15s ease, left 0.15s ease;
  z-index: 1000;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
  pointer-events: none;
}

.tooltip-content::before,
.tooltip-content::after {
  content: '';
  position: absolute;
  top: 50%;
  right: 100%;
  transform: translateY(-50%);
  border-style: solid;
  pointer-events: none;
}

.tooltip-content::after {
  border-width: 5px;
  border-color: transparent var(--border-strong, #555) transparent transparent;
}

.tooltip-content::before {
  border-width: 4px;
  border-color: transparent var(--surface-panel, #333) transparent transparent;
  z-index: 1;
  margin-right: -1px;
}

.tooltip-wrapper:hover .tooltip-content {
  opacity: 1;
  visibility: visible;
  left: calc(100% + 8px);
}

.form-actions {
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
}
</style>
