<script setup lang="ts">
import SettingsBreadcrumb from './SettingsBreadcrumb.vue';
import type { SettingsBreadcrumbItem } from './types';

type DriverState = {
  key: string;
  label: string;
  available: boolean;
  note: string;
};

defineProps<{
  breadcrumbItems: SettingsBreadcrumbItem[];
  connectionState: string;
  systemVersion: string;
  systemPlatform: string;
  uptimeLabel: string;
  teamCount: number;
  memberCount: number;
  agentCount: number;
  totalMessageCount: number;
  driverStates: DriverState[];
}>();

const emit = defineEmits<{
  navigateBreadcrumb: [key: string];
}>();
</script>

<template>
  <section id="general" class="config-section">
    <SettingsBreadcrumb :items="breadcrumbItems" @navigate="emit('navigateBreadcrumb', $event)" />

    <div class="section-head section-head--compact">
      <span class="section-status">{{ connectionState === 'connected' ? '已连接' : '状态采集中' }}</span>
    </div>

    <div class="status-grid">
      <article class="status-card">
        <span>软件版本</span>
        <strong>{{ systemVersion }}</strong>
      </article>
      <article class="status-card">
        <span>系统平台</span>
        <strong>{{ systemPlatform }}</strong>
      </article>
      <article class="status-card">
        <span>运行时间</span>
        <strong>{{ uptimeLabel }}</strong>
      </article>
    </div>

    <div class="metric-grid">
      <article class="metric-card">
        <span>团队数量</span>
        <strong>{{ teamCount }}</strong>
      </article>
      <article class="metric-card">
        <span>团队成员数量</span>
        <strong>{{ memberCount }}</strong>
      </article>
      <article class="metric-card">
        <span>Agent 数量</span>
        <strong>{{ agentCount }}</strong>
      </article>
      <article class="metric-card">
        <span>消息数量</span>
        <strong>{{ totalMessageCount }}</strong>
      </article>
    </div>

    <section class="driver-card">
      <div class="driver-head">
        <div>
          <h4>底层驱动状态</h4>
        </div>
      </div>
      <div class="driver-list">
        <div v-for="driver in driverStates" :key="driver.key" class="driver-row">
          <div class="driver-meta">
            <strong>{{ driver.label }}</strong>
            <span>{{ driver.note }}</span>
          </div>
          <span class="driver-badge" :class="{ online: driver.available }">
            {{ driver.available ? '可用' : '不可用' }}
          </span>
        </div>
      </div>
    </section>
  </section>
</template>

<style scoped>
.status-card,
.metric-card,
.driver-card {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: var(--surface-soft);
}

.config-section {
  padding: 12px 0 0;
}

.section-head,
.driver-head {
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

.section-head h3,
.driver-head h4 {
  margin: 0;
  color: var(--text-strong);
}

.driver-head h4 {
  font-size: 1rem;
}

.section-status,
.status-card span,
.metric-card span {
  color: var(--muted);
}

.status-grid,
.metric-grid {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.status-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.metric-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.status-card,
.metric-card,
.driver-card {
  padding: 10px;
}

.status-card strong,
.metric-card strong {
  display: block;
  margin-top: 4px;
  color: var(--text-strong);
  line-height: 1.35;
}

.metric-card strong {
  font-size: 1.32rem;
}

.driver-card {
  margin-top: 10px;
}

.driver-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}

.driver-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--panel-border);
  border-radius: 12px;
  background: var(--panel-bg);
}

.driver-meta strong {
  display: block;
  color: var(--text-strong);
}

.driver-meta span {
  display: block;
  margin-top: 2px;
  color: var(--muted);
  font-size: 0.74rem;
}

.driver-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 64px;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(248, 81, 73, 0.12);
  color: var(--danger);
  font-size: 0.74rem;
  font-weight: 600;
}

.driver-badge.online {
  background: rgba(86, 212, 176, 0.14);
  color: var(--good);
}

@media (max-width: 780px) {
  .status-grid,
  .metric-grid {
    grid-template-columns: 1fr;
  }
}
</style>
