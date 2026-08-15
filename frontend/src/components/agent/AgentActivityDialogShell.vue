<script setup lang="ts">
defineProps<{
  open: boolean;
  closeLabel: string;
  activeTab: 'activities' | 'tasks' | 'properties';
  panelTabsLabel: string;
  activitiesLabel: string;
  tasksLabel: string;
  propertiesLabel: string;
  activityBadgeLabel: string;
  activityRealtimeState: string;
  taskCountLabel: string;
  errorMessage: string;
  loadingMessage: string;
  showLoading: boolean;
  showStage: boolean;
  showSupervise: boolean;
}>();

const emit = defineEmits<{
  close: [];
  'update:activeTab': [tab: 'activities' | 'tasks' | 'properties'];
}>();
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="agent-detail-overlay" @click.self="emit('close')">
      <section class="agent-detail-dialog panel">
        <div class="agent-detail-head">
          <div class="agent-detail-head__left">
            <p class="agent-detail-eyebrow">Agent Status Card</p>
          </div>
          <div v-if="showStage" class="agent-detail-head__center">
            <div class="agent-activity-panel__tabs" role="tablist" :aria-label="panelTabsLabel">
              <button
                type="button"
                class="agent-activity-panel__tab"
                :class="{ 'is-active': activeTab === 'activities' }"
                :aria-selected="activeTab === 'activities'"
                @click="emit('update:activeTab', 'activities')"
              >
                {{ activitiesLabel }}
              </button>
              <button
                type="button"
                class="agent-activity-panel__tab"
                :class="{ 'is-active': activeTab === 'tasks' }"
                :aria-selected="activeTab === 'tasks'"
                @click="emit('update:activeTab', 'tasks')"
              >
                {{ tasksLabel }}
              </button>
              <button
                type="button"
                class="agent-activity-panel__tab"
                :class="{ 'is-active': activeTab === 'properties' }"
                :aria-selected="activeTab === 'properties'"
                @click="emit('update:activeTab', 'properties')"
              >
                {{ propertiesLabel }}
              </button>
            </div>
          </div>
          <div class="agent-detail-head__right">
            <span
              v-if="showStage && activeTab === 'activities'"
              class="agent-activity-panel__badge"
              :data-state="activityRealtimeState"
            >
              <span class="agent-activity-panel__badge-dot"></span>
              {{ activityBadgeLabel }}
            </span>
            <span
              v-else-if="showStage"
              class="agent-activity-panel__badge agent-activity-panel__badge--count"
            >
              {{ taskCountLabel }}
            </span>
            <button type="button" class="agent-detail-close" :aria-label="closeLabel" @click="emit('close')">×</button>
          </div>
        </div>

        <div v-if="errorMessage" class="error-banner">{{ errorMessage }}</div>
        <div v-else-if="showLoading" class="loading-card">{{ loadingMessage }}</div>

        <section v-else-if="showStage" class="agent-detail-stage">
          <div class="agent-detail-stage__left">
            <slot name="left" />
          </div>
          <div class="agent-detail-stage__right">
            <section class="agent-activity-panel">
              <div class="agent-activity-panel__body">
                <slot name="panel" />
              </div>
            </section>

            <slot v-if="showSupervise" name="supervise" />
          </div>
        </section>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.agent-detail-overlay {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(112, 133, 160, 0.22);
  backdrop-filter: blur(6px);
}

.agent-detail-dialog {
  --agent-divider-vertical: color-mix(in srgb, var(--panel-border) 92%, var(--border-subtle) 8%);
  --agent-divider-strong: color-mix(in srgb, var(--panel-border) 84%, var(--border-subtle) 16%);
  --agent-divider-emphasis: color-mix(in srgb, var(--panel-border) 94%, var(--border-subtle) 6%);
  
  /* Divider glow tokens */
  --agent-divider-glow-bg: var(--accent);
  --agent-divider-glow-shadow-1: var(--interactive-focus-ring);
  --agent-divider-glow-shadow-2: var(--accent);

  position: relative;
  width: min(1180px, calc(100vw - 40px));
  height: min(820px, calc(100vh - 40px));
  max-height: min(820px, calc(100vh - 40px));
  overflow: hidden;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 0;
  padding: 10px 16px 16px;
  border-radius: 20px;
  box-shadow:
    0 20px 48px rgba(40, 67, 102, 0.16),
    inset 0 0 0 1px color-mix(in srgb, var(--panel-border) 88%, transparent);
}



.agent-detail-dialog::before {
  content: '';
  position: absolute;
  top: 14px;
  bottom: 16px;
  left: 264px;
  width: 1px;
  background: linear-gradient(
    to bottom,
    transparent 0%,
    var(--agent-divider-glow-bg) 15%,
    var(--agent-divider-glow-bg) 85%,
    transparent 100%
  );
  filter: 
    drop-shadow(0 0 6px var(--agent-divider-glow-shadow-1))
    drop-shadow(0 0 2px var(--agent-divider-glow-shadow-2));
  pointer-events: none;
  z-index: 10;
}

.agent-detail-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 10px;
}

.agent-detail-head__left,
.agent-detail-head__right {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.agent-detail-head__left {
  flex: 0 0 248px;
  padding-right: 18px;
}

.agent-detail-head__center {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  padding-left: 18px;
}

.agent-detail-head__right {
  flex: 0 0 auto;
  margin-left: auto;
}

.agent-detail-close {
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--muted);
  font-size: 1.2rem;
  line-height: 1;
  cursor: pointer;
}

.agent-detail-close:hover {
  background: color-mix(in srgb, var(--surface-soft) 90%, transparent);
  color: var(--text-strong);
}

.agent-detail-eyebrow {
  margin: 0 0 2px;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
}

.agent-detail-stage,
.loading-card {
  background: transparent;
}

.agent-detail-stage {
  min-height: 0;
  height: 100%;
  display: grid;
  grid-template-columns: 248px minmax(0, 1fr);
  gap: 0;
  align-items: stretch;
  padding: 0;
  overflow: hidden;
}

.agent-detail-stage__left {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 0;
  height: 100%;
  padding: 0 18px 0 0;
}

.agent-detail-stage__right {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 0;
  height: 100%;
  position: relative;
  padding-left: 18px;
}

.agent-detail-stage__right::before {
  content: '';
  position: absolute;
  top: 0;
  left: 18px;
  right: 0;
  height: 1px;
  background: var(--agent-divider-emphasis);
  box-shadow: 0 1px 0 color-mix(in srgb, var(--agent-divider-emphasis) 24%, transparent);
  z-index: 2;
  pointer-events: none;
}

.agent-activity-panel {
  min-height: 0;
  flex: 1;
  border-radius: 0;
  padding: 0;
  background: transparent;
  border: 0;
  box-shadow: none;
  display: flex;
  flex-direction: column;
}

.agent-activity-panel__tabs {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.agent-activity-panel__tab {
  height: 28px;
  padding: 0 12px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--muted);
  font-size: 0.88rem;
  font-weight: 500;
  cursor: pointer;
  transition: color 160ms ease, background 160ms ease;
}

.agent-activity-panel__tab:hover {
  color: var(--text-strong);
  background: var(--surface-pill);
}

.agent-activity-panel__tab.is-active {
  color: var(--accent);
  background: var(--interactive-selected);
  font-weight: 600;
}

.agent-activity-panel__badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  background: rgba(125, 163, 224, 0.12);
  color: color-mix(in srgb, var(--accent) 78%, var(--text) 22%);
  font-size: 0.68rem;
  font-weight: 600;
}

.agent-activity-panel__badge--count {
  background: color-mix(in srgb, var(--surface-pill) 84%, var(--surface-panel-muted) 16%);
  color: var(--text-secondary);
}

.agent-activity-panel__badge[data-state='connected'] {
  color: var(--good);
}

.agent-activity-panel__badge[data-state='waiting_reconnect'],
.agent-activity-panel__badge[data-state='reconnecting'] {
  color: var(--warn);
}

.agent-activity-panel__badge[data-state='disconnected'] {
  color: var(--danger);
}

.agent-activity-panel__badge-dot {
  position: relative;
  width: 10px;
  height: 10px;
  flex: 0 0 10px;
  color: var(--status-dot-idle);
}

.agent-activity-panel__badge-dot::before {
  content: '';
  position: absolute;
  inset: 1.5px;
  border-radius: 999px;
  background: currentColor;
}

.agent-activity-panel__badge[data-state='connected'] .agent-activity-panel__badge-dot {
  color: var(--good);
}

.agent-activity-panel__badge[data-state='waiting_reconnect'] .agent-activity-panel__badge-dot,
.agent-activity-panel__badge[data-state='reconnecting'] .agent-activity-panel__badge-dot,
.agent-activity-panel__badge[data-state='connecting'] .agent-activity-panel__badge-dot {
  color: var(--warn);
}

.agent-activity-panel__badge[data-state='disconnected'] .agent-activity-panel__badge-dot {
  color: var(--danger);
}

.agent-activity-panel__body {
  min-height: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.loading-card,
.error-banner {
  padding: 14px;
  margin: 0 10px 10px;
}

.loading-card {
  border: 1px solid var(--panel-border);
  border-radius: 14px;
  background: var(--surface-soft);
}

.error-banner {
  border-radius: 10px;
  background: var(--banner-error-bg);
  color: var(--banner-error-text);
  border: 1px solid var(--banner-error-border);
}

@media (max-width: 720px) {
  .agent-detail-overlay {
    padding: 12px;
  }

  .agent-detail-dialog {
    width: min(100vw - 24px, 100%);
    height: min(100vh - 24px, 100%);
    max-height: calc(100vh - 24px);
    padding: 0 14px 14px;
    gap: 6px;
  }

  .agent-detail-head {
    flex-wrap: wrap;
    align-items: flex-start;
    padding-top: 10px;
  }

  .agent-detail-head__left,
  .agent-detail-head__center,
  .agent-detail-head__right {
    flex: 1 1 100%;
    padding-right: 0;
    padding-left: 0;
    border-left: 0;
    justify-content: flex-start;
  }

  .agent-detail-head__right {
    margin-left: 0;
  }

  .agent-detail-dialog::before {
    display: none;
  }

  .agent-detail-stage {
    grid-template-columns: 1fr;
    min-height: 0;
    padding: 8px 0 0;
    gap: 12px;
  }

  .agent-detail-stage__right {
    min-height: 180px;
    padding-left: 0;
    border-left: 0;
  }

  .agent-detail-stage__right::before {
    left: 0;
  }

  .agent-detail-stage__left {
    align-items: flex-start;
    justify-content: stretch;
    padding-right: 0;
  }

  .agent-activity-panel {
    min-height: 0;
    padding: 0;
  }
}
</style>

<style>
:root[data-theme='light'] .agent-detail-dialog {
  --agent-divider-glow-bg: var(--border-strong);
  --agent-divider-glow-shadow-1: var(--interactive-focus-ring);
  --agent-divider-glow-shadow-2: var(--border-strong);
}
</style>
