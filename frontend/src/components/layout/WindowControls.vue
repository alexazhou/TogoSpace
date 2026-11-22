<template>
  <div class="window-controls">
    <button
      type="button"
      class="window-control"
      :aria-label="t('common.minimize')"
      :title="t('common.minimize')"
      @click="minimize"
    >
      <svg viewBox="0 0 10 10" aria-hidden="true">
        <path d="M0 5h10" />
      </svg>
    </button>
    <button
      type="button"
      class="window-control"
      :aria-label="isMaximized ? t('common.restore') : t('common.maximize')"
      :title="isMaximized ? t('common.restore') : t('common.maximize')"
      @click="toggleMaximize"
    >
      <svg v-if="isMaximized" viewBox="0 0 10 10" aria-hidden="true">
        <path d="M2.5 2.5V0.5h7v7h-2" />
        <rect x="0.5" y="2.5" width="7" height="7" />
      </svg>
      <svg v-else viewBox="0 0 10 10" aria-hidden="true">
        <rect x="0.5" y="0.5" width="9" height="9" />
      </svg>
    </button>
    <button
      type="button"
      class="window-control window-control--close"
      :aria-label="t('common.close')"
      :title="t('common.close')"
      @click="closeWindow"
    >
      <svg viewBox="0 0 10 10" aria-hidden="true">
        <path d="M0 0l10 10M10 0L0 10" />
      </svg>
    </button>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { useI18n } from 'vue-i18n';

/** Windows 无边框窗口的自绘窗口按钮，仅在 Tauri Windows 端渲染。 */
const { t } = useI18n();
const isMaximized = ref(false);
let unlisten: (() => void) | null = null;

async function getWindow() {
  const { getCurrentWindow } = await import('@tauri-apps/api/window');
  return getCurrentWindow();
}

async function minimize(): Promise<void> {
  try {
    await (await getWindow()).minimize();
  } catch (error) {
    console.warn('minimize window failed:', error);
  }
}

async function toggleMaximize(): Promise<void> {
  try {
    await (await getWindow()).toggleMaximize();
  } catch (error) {
    console.warn('toggle maximize window failed:', error);
  }
}

async function closeWindow(): Promise<void> {
  try {
    await (await getWindow()).close();
  } catch (error) {
    console.warn('close window failed:', error);
  }
}

onMounted(async () => {
  try {
    const win = await getWindow();
    isMaximized.value = await win.isMaximized();
    unlisten = await win.onResized(async () => {
      isMaximized.value = await win.isMaximized();
    });
  } catch (error) {
    console.warn('listen window state failed:', error);
  }
});

onBeforeUnmount(() => {
  if (unlisten) {
    unlisten();
  }
});
</script>

<style scoped>
.window-controls {
  position: fixed;
  top: 0;
  right: 0;
  z-index: 1000;
  display: flex;
  height: var(--windows-titlebar-height, 32px);
}

.window-control {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 46px;
  height: 100%;
  padding: 0;
  border: none;
  border-radius: 0;
  background: transparent;
  color: var(--text-secondary);
  cursor: default;
  transition:
    background-color 0.15s ease,
    color 0.15s ease;
}

.window-control:hover {
  background: color-mix(in srgb, var(--text-primary) 10%, transparent);
  color: var(--text-primary);
}

.window-control:focus-visible {
  outline: 1px solid var(--interactive-focus-border);
  outline-offset: -2px;
}

.window-control--close:hover {
  background: #c42b1c;
  color: #ffffff;
}

.window-control svg {
  width: 10px;
  height: 10px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1;
  shape-rendering: crispEdges;
}
</style>
