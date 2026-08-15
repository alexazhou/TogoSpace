<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { getSkills } from '../../api';
import type { SkillInfo } from '../../types';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import SkillDetailDialog from './SkillDetailDialog.vue';
import type { SettingsBreadcrumbItem } from './types';

defineProps<{
  breadcrumbItems: SettingsBreadcrumbItem[];
}>();

const emit = defineEmits<{
  navigateBreadcrumb: [key: string];
}>();

const { t } = useI18n();

const skills = ref<SkillInfo[]>([]);
const isLoading = ref(false);

const dialogOpen = ref(false);
const selectedSkill = ref<SkillInfo | null>(null);

function truncateDesc(desc: string): string {
  if (!desc) return t('common.none');
  return desc.length > 60 ? desc.slice(0, 60) + '...' : desc;
}

function openDialog(skill: SkillInfo): void {
  selectedSkill.value = skill;
  dialogOpen.value = true;
}

function closeDialog(): void {
  dialogOpen.value = false;
  selectedSkill.value = null;
}

async function loadSkills(): Promise<void> {
  isLoading.value = true;
  try {
    const data = await getSkills();
    skills.value = data;
  } catch (error) {
    console.error(error);
  } finally {
    isLoading.value = false;
  }
}

onMounted(() => {
  void loadSkills();
});
</script>

<template>
  <section id="skills" class="config-section">
    <SettingsBreadcrumb :items="breadcrumbItems" @navigate="emit('navigateBreadcrumb', $event)" />

    <div class="section-head section-head--compact">
      <div class="section-actions">
      </div>
    </div>

    <section class="roles-table-section">
      <div class="skills-info-banner">
        <span class="info-icon">ⓘ</span>
        <span>{{ t('settings.skills.restartPrompt') }}</span>
      </div>

      <p v-if="isLoading" class="roles-empty">{{ t('settings.skills.loading') }}</p>

      <div v-else-if="skills.length" class="settings-table-wrap">
        <table class="settings-table roles-table">
          <thead>
            <tr>
              <th class="skills-cell-name">{{ t('settings.skills.table.name') }}</th>
              <th class="skills-cell-type">{{ t('settings.skills.table.type') }}</th>
              <th class="skills-cell-desc">{{ t('settings.skills.table.description') }}</th>
              <th class="skills-cell-actions">{{ t('settings.skills.table.actions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="skill in skills" :key="skill.name">
              <td class="roles-cell-name">
                <strong>{{ skill.name }}</strong>
              </td>
              <td class="skills-cell-type">
                <span
                  class="role-chip"
                  :class="skill.is_builtin ? 'role-chip--system' : 'role-chip--user'"
                >
                  {{ skill.is_builtin ? t('settings.skills.builtin') : t('settings.skills.userCustom') }}
                </span>
              </td>
              <td class="skills-cell-desc" :title="skill.description">{{ truncateDesc(skill.description) }}</td>
              <td class="skills-cell-actions">
                <button type="button" class="ghost-button" @click="openDialog(skill)">
                  {{ t('settings.skills.view') }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p v-else class="roles-empty">{{ t('settings.skills.empty') }}</p>
    </section>

    <SkillDetailDialog
      :open="dialogOpen"
      :skill="selectedSkill"
      @close="closeDialog"
    />
  </section>
</template>

<style scoped>
.config-section {
  padding: 12px 0 0;
}

.section-head,
.section-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.section-head {
  margin-bottom: 8px;
}

.section-head--compact {
  justify-content: flex-end;
}

.roles-empty {
  color: var(--muted);
}

.roles-table-section {
  margin-top: 10px;
  padding: 0 10px;
}

.skills-info-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  margin-bottom: 12px;
  background: color-mix(in srgb, var(--state-warning) 10%, var(--surface-panel) 90%);
  border: 1px solid color-mix(in srgb, var(--state-warning) 30%, var(--border-default) 70%);
  border-radius: 12px;
  color: color-mix(in srgb, var(--text-primary) 85%, var(--state-warning) 15%);
  font-size: 0.86rem;
}

.skills-info-banner .info-icon {
  color: var(--state-warning);
  font-size: 1.1em;
}

.settings-table-wrap {
  margin-top: 10px;
  overflow-x: auto;
  padding: 10px 12px 12px;
  border-radius: 16px;
  background: var(--settings-table-surface);
}

.roles-empty {
  margin-top: 10px;
  font-size: 0.86rem;
}

.settings-table {
  width: 100%;
  min-width: 0;
  border-collapse: separate;
  border-spacing: 0;
  table-layout: fixed;
}

.settings-table th,
.settings-table td {
  padding: 12px 14px;
  text-align: left;
  vertical-align: top;
}

.settings-table thead th {
  position: relative;
  padding-top: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid color-mix(in srgb, var(--divider) 86%, transparent);
  background: var(--settings-table-head-bg);
  color: var(--text-strong);
  font-size: 0.84rem;
  font-weight: 700;
  letter-spacing: 0.01em;
  white-space: nowrap;
}

.settings-table thead th:not(:last-child)::after {
  content: '';
  position: absolute;
  top: 14px;
  right: 0;
  width: 1px;
  height: calc(100% - 28px);
  background: color-mix(in srgb, var(--divider) 88%, transparent);
}

.settings-table tbody td {
  border-bottom: 1px solid color-mix(in srgb, var(--divider) 76%, transparent);
  color: var(--text-strong);
  font-size: 0.84rem;
  transition:
    background 140ms ease,
    box-shadow 140ms ease;
}

.settings-table tbody tr:hover td {
  background: var(--settings-table-row-hover);
}

.settings-table tbody tr:last-child td {
  border-bottom: none;
}

.settings-table tbody tr:first-child td {
  padding-top: 18px;
}

.roles-cell-name strong {
  color: var(--text-strong);
  font-size: 0.96rem;
}

.skills-cell-name {
  width: 180px;
}

.skills-cell-type {
  width: 120px;
}

.skills-cell-desc {
  color: var(--muted);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skills-cell-actions {
  width: 88px;
  text-align: right;
}

.skills-cell-actions .ghost-button {
  white-space: nowrap;
}

.role-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--panel-border);
  background: var(--panel-bg);
  color: var(--muted);
  font-size: 0.74rem;
  white-space: nowrap;
}

.role-chip--system {
  border-color: color-mix(in srgb, var(--focus-border) 26%, var(--panel-border) 74%);
  background: var(--backend-selected-strong, color-mix(in srgb, var(--selected) 72%, var(--panel-bg) 28%));
  color: color-mix(in srgb, var(--text-strong) 82%, var(--accent) 18%);
}

.role-chip--user {
  border-color: color-mix(in srgb, var(--state-success) 30%, var(--panel-border) 70%);
  background: color-mix(in srgb, var(--state-success) 12%, var(--panel-bg) 88%);
  color: color-mix(in srgb, var(--state-success) 90%, var(--text-strong) 10%);
}

@media (max-width: 780px) {
  .section-head,
  .section-actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .section-actions {
    width: 100%;
  }

  .settings-table {
    min-width: 720px;
  }
}
</style>
