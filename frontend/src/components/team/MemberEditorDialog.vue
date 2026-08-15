<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import AgentCardBase from '../agent/AgentCardBase.vue';
import AgentTemplateCard from '../agent/AgentTemplateCard.vue';
import CustomSelect from '../ui/CustomSelect.vue';
import InfoTooltip from '../ui/InfoTooltip.vue';
import type { MemberDriverOption, MemberModelOption, MemberTemplateOption } from '../../composables/useMemberEditorDialog';

const props = defineProps<{
  open: boolean;
  editable: boolean;
  agentId: number | null;
  teamName: string;
  memberName: string;
  status: string;
  employeeNumber: string;
  memberModel: string;
  keyword: string;
  selectedTemplateId: number | null;
  selectedTemplateName: string;
  currentTemplateModel: string;
  currentTemplateSoul: string;
  modelOptions: MemberModelOption[];
  driver: string;
  driverOptions: MemberDriverOption[];
  templateOptions: MemberTemplateOption[];
}>();

const emit = defineEmits<{
  close: [];
  save: [];
  'update:memberName': [value: string];
  'update:memberModel': [value: string];
  'update:keyword': [value: string];
  'update:selectedTemplate': [value: number | null];
  'update:driver': [value: string];
}>();

const { t } = useI18n();

const memberNameModel = computed({
  get: () => props.memberName,
  set: (value: string) => emit('update:memberName', value),
});

const memberModelValue = computed(() => props.memberModel || props.currentTemplateModel || t('common.auto'));
const memberModelModel = computed({
  get: () => props.memberModel,
  set: (value: string) => emit('update:memberModel', value),
});

const keywordModel = computed({
  get: () => props.keyword,
  set: (value: string) => emit('update:keyword', value),
});

const selectedTemplateModel = computed({
  get: () => props.selectedTemplateId,
  set: (value: number | null) => emit('update:selectedTemplate', value),
});

const driverModel = computed({
  get: () => props.driver,
  set: (value: string) => emit('update:driver', value),
});

const employeeNumberDisplay = computed(() => props.employeeNumber || t('member.employeePending'));
const canSaveMember = computed(() => Boolean(props.memberName.trim() && props.selectedTemplateId !== null));
const memberTemplateSoul = computed(() => props.currentTemplateSoul.trim() || t('member.noSoul'));
const selectedMemberAvatarSeed = computed(() => (
  props.teamName && props.memberName.trim()
    ? `${props.teamName}::${props.memberName.trim()}`
    : props.memberName.trim()
));
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="member-editor-overlay" @click.self="emit('close')">
      <section
        class="member-editor-dialog panel"
        :class="{ 'member-editor-dialog--readonly': !editable }"
      >
        <div class="member-editor-head">
          <div class="member-editor-title-row">
            <h2 class="member-editor-title">{{ t('member.title') }}</h2>
            <p class="section-eyebrow">{{ t('member.eyebrow') }}</p>
          </div>
          <p v-if="status" class="member-editor-status">{{ status }}</p>
        </div>

        <div class="member-editor-summary">
          <label class="member-editor-field">
            <span>{{ t('member.employeeId') }}</span>
            <input
              :value="employeeNumberDisplay"
              class="member-editor-input member-editor-input--readonly"
              type="text"
              readonly
            />
            <small class="member-editor-field-note">{{ t('member.employeeNote') }}</small>
          </label>
          <label class="member-editor-field">
            <span>{{ t('member.name') }}</span>
            <input
              v-model="memberNameModel"
              class="member-editor-input"
              :class="editable ? 'member-editor-input--editable' : 'member-editor-input--readonly'"
              type="text"
              :placeholder="t('member.namePlaceholder')"
              :readonly="!editable"
            />
            <small class="member-editor-field-note member-editor-field-note--placeholder" aria-hidden="true">{{ t('member.employeeNote') }}</small>
          </label>
          <label class="member-editor-field">
            <span>{{ t('member.model') }}</span>
            <CustomSelect
              v-if="editable"
              :model-value="memberModelModel"
              :options="modelOptions"
              @update:model-value="memberModelModel = $event"
            />
            <input
              v-else
              :value="memberModelValue"
              class="member-editor-input member-editor-input--readonly"
              type="text"
              readonly
            />
            <small class="member-editor-field-note member-editor-field-note--placeholder" aria-hidden="true">{{ t('member.employeeNote') }}</small>
          </label>
          <label class="member-editor-field">
            <span class="member-editor-field-label">
              {{ t('member.driver') }}
              <InfoTooltip>
                <ul class="driver-tooltip-list">
                  <li><b>自动</b>：默认等同于使用 TSP 驱动</li>
                  <li><b>TSP</b>：功能完备的协议驱动，支持自由对话与本地文件操作（推荐首选）</li>
                  <li><b>NATIVE</b>：基于 Python 实现的原生驱动，仅支持基础交谈，无法访问本地文件</li>
                  <li><b>CLAUDE</b>：使用 Claude Code 作为 Agent 的驱动，相关能力仍在开发中（暂勿选择）</li>
                </ul>
              </InfoTooltip>
            </span>
            <CustomSelect
              :model-value="driverModel"
              :options="driverOptions"
              :disabled="!editable"
              @update:model-value="driverModel = $event"
            />
            <small class="member-editor-field-note member-editor-field-note--placeholder" aria-hidden="true">{{ t('member.employeeNote') }}</small>
          </label>
        </div>

        <div class="member-editor-body">
          <section class="member-selected-panel">
            <div class="member-selected-head">
              <span class="panel-label">{{ t('member.selectedRole') }}</span>
            </div>
            <div class="member-selected-body">
              <AgentCardBase
                v-if="selectedTemplateName"
                class="member-selected-card"
                :title="memberNameModel"
                :subtitle="selectedTemplateName"
                :employee-number="employeeNumber"
                :avatar-name="memberNameModel"
                :avatar-seed="selectedMemberAvatarSeed"
                :selected="false"
                variant="graph"
              />
              <div v-else class="member-template-empty member-selected-empty">
                {{ t('member.noTemplateSelected') }}
              </div>
            </div>
          </section>

          <section class="member-soul-panel">
            <div class="member-soul-head">
              <span class="panel-label">{{ t('member.soul') }}</span>
            </div>
            <textarea
              :value="memberTemplateSoul"
              class="member-soul-input member-editor-input member-editor-input--readonly"
              readonly
            />
          </section>
        </div>

        <section v-if="editable" class="member-template-panel">
          <div class="member-template-head">
            <span class="panel-label">{{ t('member.availableRoles') }}</span>
            <label class="member-template-search">
              <input
                v-model="keywordModel"
                type="text"
                :placeholder="t('member.searchRole')"
              />
            </label>
          </div>

          <div class="member-template-grid">
            <div
              v-for="item in templateOptions"
              :key="item.id"
              class="member-template-option"
              :class="{ 'is-selected': selectedTemplateId === item.id }"
              :aria-pressed="selectedTemplateId === item.id"
            >
              <AgentTemplateCard
                :title="item.displayName"
                :subtitle="item.name"
                :avatar-name="item.name"
                :avatar-seed="item.name"
                :selected="selectedTemplateId === item.id"
              />
              <button
                type="button"
                class="member-template-use"
                :aria-pressed="selectedTemplateId === item.id"
                @click.stop="selectedTemplateModel = item.id"
              >
                {{ t('common.use') }}
              </button>
            </div>

            <div v-if="!templateOptions.length" class="member-template-empty">
              {{ t('member.noAvailableTemplates') }}
            </div>
          </div>
        </section>

        <div class="member-editor-actions">
          <div class="member-editor-actions-main">
            <button type="button" class="ghost-button" @click="emit('close')">
              {{ editable ? t('common.cancel') : t('common.close') }}
            </button>
            <button
              v-if="editable"
              type="button"
              class="secondary-button"
              :disabled="!canSaveMember"
              @click="emit('save')"
            >
              {{ t('common.save') }}
            </button>
          </div>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.member-editor-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 28px;
  background: rgba(6, 10, 16, 0.58);
  backdrop-filter: blur(8px);
}

.member-editor-dialog {
  width: min(920px, 100%);
  max-height: min(840px, calc(100vh - 40px));
  padding: 16px;
  display: grid;
  grid-template-rows: auto auto auto minmax(0, 1fr) auto;
  gap: 14px;
  border-radius: 20px;
  border: 1px solid color-mix(in srgb, var(--focus-border) 32%, var(--panel-border) 68%);
  background:
    linear-gradient(
      180deg,
      color-mix(in srgb, var(--panel-bg) 94%, transparent) 0%,
      color-mix(in srgb, var(--surface-soft) 92%, transparent) 100%
    );
  box-shadow: 0 28px 72px rgba(0, 0, 0, 0.36);
}

.member-editor-head,
.member-template-head,
.member-editor-actions {
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  gap: 12px;
}

.member-editor-head {
  display: grid;
  gap: 8px;
}

.member-editor-status {
  margin: 0;
  color: #b5523b;
  font-size: 0.76rem;
  line-height: 1.35;
}

.member-template-head {
  justify-content: space-between;
}

.member-editor-actions {
  justify-content: flex-end;
  align-items: center;
}

.member-editor-actions-main {
  display: flex;
  align-items: center;
  gap: 12px;
}

.member-editor-actions-main > button {
  min-width: 88px;
  height: 32px;
  padding: 0 14px;
  justify-content: center;
  font-size: 0.84rem;
}

.member-editor-danger-button {
  color: #c0392b;
  border-color: #c0392b;
}

.member-editor-title-row {
  display: flex;
  align-items: baseline;
  gap: 14px;
  min-width: 0;
}

.member-editor-title {
  margin: 0;
  color: var(--text-strong);
  font-size: 1.72rem;
  line-height: 1.04;
}

.section-eyebrow {
  margin: 0;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
  flex: 0 0 auto;
}

.member-editor-summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.member-editor-body {
  --member-selected-card-width: 153px;
  --member-selected-card-height: calc(var(--member-selected-card-width) * 4 / 3);
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  align-items: start;
}

.member-editor-field {
  display: grid;
  grid-template-rows: auto 36px auto;
  gap: 6px;
  align-content: start;
}

.member-editor-field span {
  color: var(--muted);
  font-size: 0.74rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.member-editor-field-label {
  display: flex;
  align-items: center;
  gap: 6px;
}

.driver-tooltip-list {
  margin: 0;
  padding: 0 0 0 16px;
  font-size: 0.72rem;
  line-height: 1.5;
  font-weight: 400;
  text-transform: none;
  letter-spacing: normal;
  white-space: normal;
}

.driver-tooltip-list li {
  margin-bottom: 3px;
}

.driver-tooltip-list li:last-child {
  margin-bottom: 0;
}
.member-editor-field-note {
  color: color-mix(in srgb, var(--muted) 78%, transparent);
  font-size: 0.66rem;
  line-height: 1.25;
  max-width: 28ch;
  margin-top: 2px;
}

.member-editor-field-note--placeholder {
  visibility: hidden;
}

.member-editor-input,
.member-template-search input {
  width: 100%;
  height: 36px;
  border-radius: 12px;
  color: var(--text-strong);
  padding: 0 12px;
  outline: none;
  transition:
    border-color 0.18s ease,
    background 0.18s ease,
    box-shadow 0.18s ease;
}

.member-editor-input--readonly {
  border: 1px dashed var(--form-input-readonly-border);
  background: var(--form-input-readonly-bg);
  color: var(--form-input-readonly-text);
  -webkit-text-fill-color: var(--form-input-readonly-text);
  box-shadow: none;
}

.member-editor-input--editable {
  border: 1px solid var(--form-input-border);
  background: var(--form-input-bg);
  box-shadow: inset 0 0 0 1px var(--form-input-selected-inner);
}

.member-editor-input--editable:focus {
  border-color: var(--focus-border);
  box-shadow:
    inset 0 0 0 1px var(--form-input-focus-inner),
    0 0 0 3px var(--form-input-focus-ring);
}

.member-editor-field select {
  appearance: none;
  cursor: pointer;
}

.member-editor-field :deep(.custom-select__button) {
  height: 36px;
  min-height: 36px;
  padding: 0 12px;
  border-radius: 12px;
  font-size: 0.82rem;
}

.member-editor-field :deep(.custom-select__option) {
  min-height: 32px;
  font-size: 0.82rem;
}

.member-editor-dialog--readonly .member-editor-field select:disabled,
.member-editor-dialog--readonly .member-template-search input:disabled {
  opacity: 1;
  cursor: default;
}

.member-editor-dialog--readonly .member-editor-field select:disabled {
  border-style: dashed;
  color: var(--form-input-readonly-text);
  -webkit-text-fill-color: var(--form-input-readonly-text);
}

.member-editor-input[readonly] {
  cursor: default;
}

.member-selected-panel {
  display: grid;
  grid-template-rows: auto auto;
  gap: 6px;
  grid-column: 1;
}

.member-soul-panel {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 6px;
  grid-column: 2 / -1;
}

.member-template-panel {
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 6px;
  padding: 8px 12px 10px;
  border: 1px solid color-mix(in srgb, var(--focus-border) 16%, var(--panel-border) 84%);
  border-radius: 16px;
  background: color-mix(in srgb, var(--surface-soft) 74%, var(--panel-bg) 26%);
}

.member-selected-head {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 12px;
  padding-left: calc((100% - var(--member-selected-card-width)) / 2);
}

.member-selected-body {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: var(--member-selected-card-height);
}

.member-selected-card {
  pointer-events: none;
  --member-card-width: var(--member-selected-card-width);
  --entity-overline-size: 0.96rem;
  --entity-title-size: 1rem;
  --entity-subtitle-size: 0.88rem;
  --entity-badge-size: 0.96rem;
}

.member-selected-empty {
  width: min(320px, 100%);
  min-height: var(--member-selected-card-height);
}

.member-soul-head {
  display: flex;
  align-items: flex-start;
}

.member-soul-input {
  min-height: var(--member-selected-card-height);
  height: var(--member-selected-card-height);
  padding: 12px;
  resize: none;
  overflow: auto;
  white-space: pre-wrap;
  line-height: 1.55;
  text-align: left;
  scrollbar-width: thin;
  scrollbar-color: var(--scrollbar-thumb) var(--scrollbar-track);
}

.member-soul-input::-webkit-scrollbar {
  width: 12px;
}

.member-soul-input::-webkit-scrollbar-track {
  background: var(--scrollbar-track);
}

.member-soul-input::-webkit-scrollbar-thumb {
  background: var(--scrollbar-thumb);
  border-radius: 999px;
  border: 2px solid var(--scrollbar-track);
}

.member-soul-input::-webkit-scrollbar-thumb:hover {
  background: var(--scrollbar-thumb-hover);
}

.member-template-search {
  width: 168px;
}

.member-template-head {
  min-height: 24px;
}

.member-template-head .panel-label {
  font-size: 0.94rem;
  padding-top: 4px;
}

.member-template-search input {
  height: 30px;
  padding: 0 10px;
  border: 1px solid color-mix(in srgb, var(--focus-border) 18%, var(--panel-border) 82%);
  border-radius: 10px;
  background: color-mix(in srgb, var(--surface-soft) 82%, var(--panel-bg) 18%);
  font-size: 0.76rem;
  box-shadow: none;
}

.member-template-search input:focus {
  border-color: var(--focus-border);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--focus-border) 14%, transparent);
}

.member-template-grid {
  --member-card-width: 102px;
  min-height: 0;
  overflow-x: auto;
  overflow-y: hidden;
  display: flex;
  flex-wrap: nowrap;
  gap: 12px;
  align-items: start;
  padding-top: 2px;
  padding-right: 4px;
  padding-bottom: 4px;
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--focus-border) 22%, var(--panel-border) 78%) transparent;
  contain: layout paint;
}

.member-template-grid::-webkit-scrollbar {
  height: 8px;
}

.member-template-grid::-webkit-scrollbar-track {
  background: transparent;
}

.member-template-grid::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: color-mix(in srgb, var(--focus-border) 22%, var(--panel-border) 78%);
}

.member-template-grid::-webkit-scrollbar-thumb:hover {
  background: color-mix(in srgb, var(--focus-border) 36%, var(--panel-border) 64%);
}

.member-template-option {
  position: relative;
  display: flex;
  align-items: stretch;
  justify-content: stretch;
  flex: 0 0 var(--member-card-width);
  width: var(--member-card-width);
}

.member-template-option > :deep(.agent-card) {
  width: 100%;
}

.member-template-option > :deep(.entity-card) {
  box-shadow: none;
}

.member-template-option > :deep(.entity-card:hover),
.member-template-option > :deep(.entity-card.selected) {
  box-shadow: none;
}

.member-template-option > :deep(.entity-card__avatar) {
  box-shadow: none;
}

.member-template-use {
  position: absolute;
  left: 6px;
  right: 6px;
  bottom: 6px;
  height: 24px;
  border: 1px solid color-mix(in srgb, var(--focus-border) 56%, var(--panel-border) 44%);
  border-radius: 8px;
  background: color-mix(in srgb, var(--selected) 88%, #fff 12%);
  color: var(--text-strong);
  font-size: 0.68rem;
  cursor: pointer;
  opacity: 0;
  transform: translateY(4px);
  transition:
    opacity 0.16s ease,
    transform 0.16s ease,
    background 0.16s ease,
    border-color 0.16s ease;
}

.member-template-option:hover .member-template-use {
  opacity: 1;
  transform: translateY(0);
}

.member-template-use:hover {
  background: color-mix(in srgb, var(--selected) 92%, #fff 8%);
  border-color: var(--focus-border);
}

.member-template-empty {
  min-height: 120px;
  flex: 0 0 100%;
  display: grid;
  place-items: center;
  color: var(--muted);
  border-radius: 14px;
  background: color-mix(in srgb, var(--surface-soft) 78%, transparent);
}

@media (max-width: 900px) {
  .member-editor-summary {
    grid-template-columns: 1fr;
  }

  .member-editor-body {
    grid-template-columns: 1fr;
  }

  .member-selected-panel,
  .member-soul-panel {
    grid-column: auto;
  }

  .member-selected-body {
    justify-content: center;
  }
}
</style>
