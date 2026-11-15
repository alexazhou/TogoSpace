<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import type { McpServerEntry } from '../../api';
import CustomSelect from '../ui/CustomSelect.vue';
import ModalDialog from '../ui/ModalDialog.vue';
import FormField from '../ui/FormField.vue';
import InfoTooltip from '../ui/InfoTooltip.vue';
import McpCredentialSection from './McpCredentialSection.vue';

export interface McpServerFormPayload {
  name: string;
  kind: string;
  protocol: string | null;
  target: string;
  note: string;
  headers: Record<string, string>;
  env: Record<string, string>;
  overwrite: boolean;
}

type EditorMode = 'create' | 'edit';

/** kind（来源是什么）带说明标签；裸 kind 值不允许直接展示（Operator 要求）。 */
const KIND_CHOICES = [
  { value: 'mcp', labelKey: 'settings.mcpServers.mcpServersKindMcp' },
  { value: 'openapi', labelKey: 'settings.mcpServers.mcpServersKindOpenapi' },
  { value: 'graphql', labelKey: 'settings.mcpServers.mcpServersKindGraphql' },
];

/** protocol（MCP 用哪种传输，仅 kind=mcp 有意义），与后端 VALID_PROTOCOLS 同集。 */
const PROTOCOL_CHOICES = [
  { value: 'streamable_http', labelKey: 'settings.mcpServers.mcpServersProtocolStreamable' },
  { value: 'sse', labelKey: 'settings.mcpServers.mcpServersProtocolSse' },
  { value: 'stdio', labelKey: 'settings.mcpServers.mcpServersProtocolStdio' },
];

const emit = defineEmits<{
  save: [payload: McpServerFormPayload];
}>();

const { t } = useI18n();

const props = defineProps<{
  submitting?: boolean;
}>();

const visible = ref(false);
const mode = ref<EditorMode>('create');
const editName = ref('');

const form = ref({
  name: '',
  kind: 'mcp',
  protocol: 'streamable_http',
  target: '',
  note: '',
});
const headers = ref<Record<string, string>>({});
const env = ref<Record<string, string>>({});

const isCreating = computed(() => mode.value === 'create');
const isMcpKind = computed(() => form.value.kind === 'mcp');
const dialogTitle = computed(() =>
  isCreating.value ? t('settings.mcpServers.mcpServersFormTitle') : editName.value,
);
const dialogEyebrow = computed(() =>
  isCreating.value
    ? t('settings.mcpServers.mcpServersFormEyebrowCreate')
    : t('settings.mcpServers.mcpServersFormEyebrowEdit'),
);

const kindChoices = computed(() =>
  KIND_CHOICES.map((choice) => ({ value: choice.value, label: t(choice.labelKey) })));
const protocolChoices = computed(() =>
  PROTOCOL_CHOICES.map((choice) => ({ value: choice.value, label: t(choice.labelKey) })));

/** 凭证层随传输走：stdio 是子进程环境变量（env），其余是 HTTP 头（headers）。 */
const credentialLayer = computed<'headers' | 'env'>(() =>
  isMcpKind.value && form.value.protocol === 'stdio' ? 'env' : 'headers');

const canSave = computed(() => form.value.name.trim().length > 0 && form.value.target.trim().length > 0);

/** 「连接目标」填写口径随类型/传输方式变化，hover i 提示。 */
const targetHint = computed(() => {
  if (form.value.kind === 'openapi') {
    return t('settings.mcpServers.mcpServersTargetHintOpenapi');
  }
  if (form.value.kind === 'graphql') {
    return t('settings.mcpServers.mcpServersTargetHintGraphql');
  }
  return form.value.protocol === 'stdio'
    ? t('settings.mcpServers.mcpServersTargetHintMcpStdio')
    : t('settings.mcpServers.mcpServersTargetHintMcpHttp');
});

// kind 切到非 mcp 时 protocol 不再有意义（后端会拒绝非空 protocol），一并清空对应层凭证。
watch(isMcpKind, (mcp) => {
  if (!mcp) {
    env.value = {};
  }
});

function closeDialog(): void {
  visible.value = false;
}

function openCreate(): void {
  mode.value = 'create';
  form.value = { name: '', kind: 'mcp', protocol: 'streamable_http', target: '', note: '' };
  headers.value = {};
  env.value = {};
  visible.value = true;
}

function openEdit(entry: McpServerEntry): void {
  mode.value = 'edit';
  editName.value = entry.name;
  form.value = {
    name: entry.name,
    kind: entry.kind,
    protocol: entry.protocol ?? 'streamable_http',
    target: entry.target,
    note: entry.note,
  };
  headers.value = { ...(entry.config?.headers ?? {}) };
  env.value = { ...(entry.config?.env ?? {}) };
  visible.value = true;
}

function handleSave(): void {
  if (!canSave.value) return;
  emit('save', {
    name: form.value.name.trim(),
    kind: form.value.kind,
    protocol: isMcpKind.value ? form.value.protocol : null,
    target: form.value.target.trim(),
    note: form.value.note,
    headers: headers.value,
    env: env.value,
    // 新建不允许覆盖已有登记（名字冲突由后端拒绝）；改已有记录走编辑，必须覆盖。
    overwrite: !isCreating.value,
  });
}

defineExpose({ openCreate, openEdit, close: closeDialog });
</script>

<template>
  <ModalDialog
    :open="visible"
    :title="dialogTitle"
    :eyebrow="dialogEyebrow"
    :width="760"
    @close="closeDialog"
  >
    <div class="gu-form-grid">
      <FormField :label="t('settings.mcpServers.mcpServersName')" wide>
        <input
          v-model="form.name"
          type="text"
          class="gu-input"
          :readonly="!isCreating"
          spellcheck="false"
        />
      </FormField>

      <FormField :label="t('settings.mcpServers.mcpServersKind')" :wide="!isMcpKind">
        <CustomSelect v-model="form.kind" :options="kindChoices" class="select-skin" />
      </FormField>

      <FormField
        v-if="isMcpKind"
        :label="t('settings.mcpServers.mcpServersProtocol')"
      >
        <CustomSelect v-model="form.protocol" :options="protocolChoices" class="select-skin" />
      </FormField>

      <FormField :label="t('settings.mcpServers.mcpServersTarget')" wide>
        <template #label>
          {{ t('settings.mcpServers.mcpServersTarget') }}
          <InfoTooltip :text="targetHint" />
        </template>
        <input v-model="form.target" type="text" class="gu-input" spellcheck="false" />
      </FormField>

      <FormField :label="t('settings.mcpServers.mcpServersNoteLabel')" wide>
        <input v-model="form.note" type="text" class="gu-input" />
      </FormField>
    </div>

    <McpCredentialSection
      :layer="credentialLayer"
      :entries="credentialLayer === 'env' ? env : headers"
      @save="credentialLayer === 'env' ? (env = $event) : (headers = $event)"
    />

    <template #footer-trailing>
      <button type="button" class="secondary-button" @click="closeDialog">
        {{ t('settings.mcpServers.mcpServersCancel') }}
      </button>
      <button type="button" class="secondary-button" :disabled="!canSave || props.submitting" @click="handleSave">
        {{ t('settings.mcpServers.mcpServersSave') }}
      </button>
    </template>
  </ModalDialog>
</template>

<style scoped>
/* 与 ProviderEditorDialog 同款 select 皮肤 */
:deep(.select-skin .custom-select__button) { min-height: 40px; height: 40px; padding: 0 12px; border-radius: 12px; font-size: 0.88rem; border-color: var(--panel-border); background: var(--panel-bg); }
:deep(.select-skin .custom-select__button:hover), :deep(.select-skin.is-open .custom-select__button) { border-color: var(--focus-border); }
:deep(.select-skin .custom-select__option) { min-height: 38px; padding: 0 12px; font-size: 0.88rem; border-radius: 8px; }
</style>
