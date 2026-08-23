<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { getSkills } from '../../api';
import type { SkillInfo } from '../../types';
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import SkillDetailDialog from './SkillDetailDialog.vue';
import type { SettingsBreadcrumbItem } from './types';
import UiTag from '../ui/UiTag.vue';

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

      <div v-else-if="skills.length" class="ui-table-wrap">
        <table class="ui-table roles-table">
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
                <UiTag :tone="skill.is_builtin ? 'info' : 'success'">
                  {{ skill.is_builtin ? t('settings.skills.builtin') : t('settings.skills.userCustom') }}
                </UiTag>
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

@media (max-width: 780px) {
  .section-head,
  .section-actions {
    align-items: flex-start;
    flex-direction: column;
  }

  .section-actions {
    width: 100%;
  }

  .ui-table {
    min-width: 720px;
  }
}
</style>
