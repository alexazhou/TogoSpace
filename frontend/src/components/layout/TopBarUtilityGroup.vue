<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import i18n from '../../i18n';
import { setLanguage } from '../../api';
import { clearToken } from '../../authStore';
import { showTokenDialog } from '../../appUiState';
import { clearTeams } from '../../teamStore';
import { clearRuntimeStore } from '../../realtime/runtimeStore';
import { openExternal } from '../../utils/platform';
import ConfirmDialog from '../ui/ConfirmDialog.vue';
import type { AppLocale } from '../../i18n';

/**
 * 顶栏「工具与更新」区块：主题切换、语言切换（含菜单与外部点击关闭）、登出按钮
 * （含二次确认弹窗）、更新可用提示。
 *
 * 从 `TopBar.vue` 拆出（P4）。注意本组件是**多根**（fragment）：`.topbar-utility-group`
 * 与 `.update-pill` 两个根节点直接作为父级 `.status-group` 的 flex 子项，DOM 顺序与拆分前一致；
 * 登出确认弹窗也是根节点之一（拆分前挂在 `<header>` 之后）。
 */
const { t } = useI18n();

const props = defineProps<{
  isLightMode: boolean;
  authEnabled?: boolean;
  hasUpdate?: boolean;
  latestVersion?: string;
  releaseUrl?: string;
}>();

const emit = defineEmits<{
  toggleTheme: [];
}>();

const languageMenuOpen = ref(false);
const currentLocale = computed<AppLocale>(() => i18n.global.locale.value);
const logoutConfirmOpen = ref(false);

function handleWindowPointerDown(event: PointerEvent): void {
  const target = event.target;
  if (!(target instanceof Element)) {
    return;
  }

  if (!target.closest('.language-switch')) {
    languageMenuOpen.value = false;
  }
}

function handleWindowKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    languageMenuOpen.value = false;
  }
}

onBeforeUnmount(() => {
  window.removeEventListener('pointerdown', handleWindowPointerDown);
  window.removeEventListener('keydown', handleWindowKeydown);
});

if (typeof window !== 'undefined') {
  window.addEventListener('pointerdown', handleWindowPointerDown);
  window.addEventListener('keydown', handleWindowKeydown);
}

function toggleLanguageMenu(): void {
  languageMenuOpen.value = !languageMenuOpen.value;
}

async function handleSetLanguage(lang: AppLocale): Promise<void> {
  try {
    await setLanguage(lang);
    i18n.global.locale.value = lang;
    languageMenuOpen.value = false;
  } catch (error) {
    console.error('Failed to set language:', error);
  }
}

function handleOpenRelease(): void {
  openExternal(props.releaseUrl || 'https://github.com/alexazhou/TogoSpace/releases');
}

function handleLogout(): void {
  logoutConfirmOpen.value = true;
}

function confirmLogout(): void {
  logoutConfirmOpen.value = false;
  clearRuntimeStore();
  clearTeams();
  clearToken();
  showTokenDialog.value = true;
}

function closeLogoutConfirm(): void {
  logoutConfirmOpen.value = false;
}
</script>

<template>
  <div class="topbar-utility-group">
    <button
      class="theme-switch"
      type="button"
      :aria-pressed="isLightMode"
      :title="isLightMode ? t('topbar.switchToDark') : t('topbar.switchToLight')"
      @click="emit('toggleTheme')"
    >
      <span
        class="theme-switch-icon theme-switch-icon-sun"
        :class="{ 'is-active': isLightMode }"
        aria-hidden="true"
      >
        <svg viewBox="0 0 24 24">
          <circle cx="12" cy="12" r="4"></circle>
          <path d="M12 2.75v2.5"></path>
          <path d="M12 18.75v2.5"></path>
          <path d="M4.93 4.93l1.77 1.77"></path>
          <path d="M17.3 17.3l1.77 1.77"></path>
          <path d="M2.75 12h2.5"></path>
          <path d="M18.75 12h2.5"></path>
          <path d="M4.93 19.07l1.77-1.77"></path>
          <path d="M17.3 6.7l1.77-1.77"></path>
        </svg>
      </span>
      <span class="theme-switch-track">
        <span class="theme-switch-thumb" :class="{ 'is-dark': !isLightMode }"></span>
      </span>
      <span
        class="theme-switch-icon theme-switch-icon-moon"
        :class="{ 'is-active': !isLightMode }"
        aria-hidden="true"
      >
        <svg viewBox="0 0 24 24">
          <path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a7.8 7.8 0 1 0 10.5 10.5Z"></path>
        </svg>
      </span>
    </button>
    <div class="language-switch">
      <button
        type="button"
        class="lang-button"
        :aria-expanded="languageMenuOpen"
        aria-haspopup="listbox"
        :aria-label="t('language.switcher')"
        :title="t('language.switcher')"
        @click="toggleLanguageMenu"
      >
        <svg class="lang-button__globe" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18Z" />
          <path d="M3.6 9h16.8" />
          <path d="M3.6 15h16.8" />
          <path d="M12 3c2.3 2.2 3.6 5.3 3.6 9s-1.3 6.8-3.6 9c-2.3-2.2-3.6-5.3-3.6-9s1.3-6.8 3.6-9Z" />
        </svg>
        <svg class="lang-button__chevron" viewBox="0 0 16 16" aria-hidden="true">
          <path d="m4 6 4 4 4-4" />
        </svg>
      </button>
      <div
        v-if="languageMenuOpen"
        class="lang-menu"
        role="listbox"
      >
        <button
          type="button"
          class="lang-option"
          :class="{ 'is-active': currentLocale === 'zh-CN' }"
          role="option"
          :aria-selected="currentLocale === 'zh-CN'"
          @click="handleSetLanguage('zh-CN')"
        >
          <span class="lang-option__check" aria-hidden="true">{{ currentLocale === 'zh-CN' ? '✓' : '' }}</span>
          {{ t('language.zhCN') }}
        </button>
        <button
          type="button"
          class="lang-option"
          :class="{ 'is-active': currentLocale === 'en' }"
          role="option"
          :aria-selected="currentLocale === 'en'"
          @click="handleSetLanguage('en')"
        >
          <span class="lang-option__check" aria-hidden="true">{{ currentLocale === 'en' ? '✓' : '' }}</span>
          {{ t('language.en') }}
        </button>
      </div>
    </div>
    <button
      v-if="authEnabled"
      class="nav-icon-button toolbar-icon-button logout-button"
      type="button"
      :title="t('topbar.logout')"
      :aria-label="t('topbar.logout')"
      @click="handleLogout"
    >
      <svg class="logout-button__icon" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M10 4.5H7.25A2.25 2.25 0 0 0 5 6.75v10.5a2.25 2.25 0 0 0 2.25 2.25H10" />
        <path d="M13 8.5 17 12l-4 3.5" />
        <path d="M9 12h8" />
      </svg>
    </button>
  </div>
  <a
    v-if="hasUpdate"
    :href="releaseUrl || 'https://github.com/alexazhou/TogoSpace/releases'"
    target="_blank"
    rel="noopener"
    class="update-pill"
    @click.prevent="handleOpenRelease"
  >
    <span class="update-pill__dot"></span>
    {{ t('topbar.updateAvailable', { version: latestVersion }) }}
  </a>
  <ConfirmDialog
    :open="logoutConfirmOpen"
    :title="t('topbar.logoutConfirmTitle')"
    :message="t('topbar.logoutConfirmMsg')"
    :confirm-label="t('topbar.logout')"
    @close="closeLogoutConfirm"
    @confirm="confirmLogout"
  />
</template>

<style scoped>
.nav-icon-button {
  appearance: none;
  -webkit-appearance: none;
  width: 24px;
  height: 24px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  cursor: pointer;
  outline: none;
  box-shadow: none;
  transition:
    color 140ms ease,
    background 140ms ease,
    box-shadow 140ms ease;
}

.nav-action:disabled,
.nav-icon-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.toolbar-icon-button {
  width: 28px;
  height: 28px;
  border: 1px solid var(--border-subtle);
  background: var(--surface-pill);
  color: var(--text-secondary);
}

.toolbar-icon-button:not(:disabled):hover {
  border-color: var(--interactive-focus-border);
  background: color-mix(in srgb, var(--interactive-selected) 28%, var(--surface-pill) 72%);
  color: var(--text-primary);
}

.team-switcher-button:focus-visible,
.nav-action:focus-visible,
.nav-icon-button:focus-visible,
.theme-switch:focus-visible {
  border-color: var(--interactive-focus-border);
  box-shadow: 0 0 0 2px var(--interactive-focus-ring);
}

.topbar-utility-group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.update-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid color-mix(in srgb, var(--state-info) 35%, var(--border-subtle) 65%);
  border-radius: 8px;
  padding: 3px 10px;
  background: color-mix(in srgb, var(--state-info) 14%, var(--surface-pill) 86%);
  color: color-mix(in srgb, var(--state-info) 85%, var(--text-primary) 15%);
  font-size: 0.72rem;
  font-weight: 600;
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    color 140ms ease;
}

.update-pill:hover {
  border-color: color-mix(in srgb, var(--state-info) 55%, var(--interactive-focus-border) 45%);
  background: color-mix(in srgb, var(--state-info) 22%, var(--surface-pill) 78%);
  color: var(--text-primary);
}

.update-pill__dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--state-info);
}

.theme-switch {
  appearance: none;
  -webkit-appearance: none;
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 28px;
  padding: 0 5px;
  border: 1px solid var(--border-subtle);
  border-radius: 999px;
  background: var(--surface-pill);
  color: var(--text-secondary);
  cursor: pointer;
  outline: none;
  box-shadow: none;
  transition:
    border-color 140ms ease,
    background 140ms ease,
    color 140ms ease;
}

.theme-switch:hover {
  border-color: var(--interactive-focus-border);
  color: var(--text-primary);
}

.theme-switch svg {
  width: 12px;
  height: 12px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.nav-icon-button i {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  line-height: 1;
}

.logout-button__icon {
  width: 16px;
  height: 16px;
  fill: none;
  stroke: currentColor;
  stroke-width: 2.1;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.theme-switch-icon {
  color: var(--text-secondary);
  transition: color 160ms ease;
}

.theme-switch-icon.is-active {
  color: var(--theme-switch-icon-active);
}

.theme-switch-track {
  position: relative;
  width: 28px;
  height: 18px;
  border-radius: 999px;
  background: var(--toolbar-switch-off);
}

.theme-switch-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  border-radius: 999px;
  background: var(--toolbar-switch-handle);
  transition: transform 180ms ease;
}

.theme-switch-thumb.is-dark {
  transform: translateX(10px);
}

.language-switch {
  position: relative;
  display: flex;
  align-items: center;
}

.lang-button {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  height: 28px;
  padding: 0 5px 0 6px;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  background: var(--surface-pill);
  color: var(--text-primary);
  cursor: pointer;
  outline: none;
  transition: border-color 140ms ease, background 140ms ease;
}

.lang-button:hover {
  border-color: var(--interactive-focus-border);
}

.lang-button:focus-visible {
  border-color: var(--interactive-focus-border);
  box-shadow: 0 0 0 2px var(--interactive-focus-ring);
}

.lang-button__globe,
.lang-button__chevron {
  fill: none;
  stroke: var(--text-secondary);
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.lang-button__globe {
  width: 16px;
  height: 16px;
}

.lang-button__chevron {
  width: 10px;
  height: 10px;
}

.lang-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 24;
  min-width: 100px;
  padding: 4px;
  display: grid;
  gap: 2px;
  border: 1px solid var(--border-default);
  border-radius: 8px;
  background: var(--surface-overlay);
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.14);
}

.lang-option {
  display: grid;
  grid-template-columns: 14px 1fr;
  align-items: center;
  gap: 8px;
  width: 100%;
  min-height: 28px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--text-primary);
  font-size: 0.72rem;
  text-align: left;
  cursor: pointer;
  outline: none;
}

.lang-option__check {
  color: var(--text-secondary);
  font-size: 0.76rem;
  font-weight: 700;
  text-align: center;
}

.lang-option:hover,
.lang-option:focus-visible {
  border-color: color-mix(in srgb, var(--interactive-focus-border) 42%, transparent);
  background: color-mix(in srgb, var(--interactive-selected) 56%, var(--surface-panel) 44%);
}

.lang-option.is-active {
  border-color: color-mix(in srgb, var(--interactive-focus-border) 56%, var(--border-subtle) 44%);
  background: color-mix(in srgb, var(--interactive-selected) 72%, var(--surface-panel) 28%);
  font-weight: 600;
}

.logout-button {
  flex: 0 0 auto;
}

:global(html.bp-console-mobile) .topbar.topbar-console .topbar-utility-group {
  margin-left: auto;
  gap: 4px;
  flex: 0 0 auto;
}

:global(html.bp-console-mobile) .topbar.topbar-console .theme-switch,
:global(html.bp-console-mobile) .topbar.topbar-console .lang-button,
:global(html.bp-console-mobile) .topbar.topbar-console .toolbar-icon-button {
  height: 32px;
}

:global(html.bp-console-mobile) .topbar.topbar-console .toolbar-icon-button {
  width: 32px;
}

:global(html.bp-console-short) .topbar.topbar-console .nav-icon-button--bare,
:global(html.bp-console-short) .topbar.topbar-console .team-switcher-button,
:global(html.bp-console-short) .topbar.topbar-console .theme-switch,
:global(html.bp-console-short) .topbar.topbar-console .lang-button,
:global(html.bp-console-short) .topbar.topbar-console .toolbar-icon-button {
  height: 28px;
}

:global(html.bp-console-short) .topbar.topbar-console .nav-icon-button--bare,
:global(html.bp-console-short) .topbar.topbar-console .toolbar-icon-button {
  width: 28px;
}
</style>
