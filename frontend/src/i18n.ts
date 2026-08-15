import { createI18n } from 'vue-i18n';
import { setGlobalRequestErrorAutoDismiss } from './appUiState';
import zhCN from './locales/zh-CN.json';
import en from './locales/en.json';

export const supportedLocales = ['zh-CN', 'en'] as const;
export type AppLocale = (typeof supportedLocales)[number];

export function isAppLocale(value: string): value is AppLocale {
  return supportedLocales.includes(value as AppLocale);
}

const i18n = createI18n({
  legacy: false,
  locale: 'zh-CN' as AppLocale,
  fallbackLocale: 'zh-CN' as AppLocale,
  messages: { 'zh-CN': zhCN, en },
});

export default i18n;

/** Standalone translation function for use outside Vue components (stores, utils, api). */
export function t(key: string, params?: Record<string, string | number>): string {
  return i18n.global.t(key, params ?? {}) as string;
}

export async function syncLanguageFromBackend(): Promise<void> {
  try {
    const resp = await fetch('/system/status.json');
    if (resp.ok) {
      const data = await resp.json() as { language?: string; development_mode?: boolean };
      if (data.language && isAppLocale(data.language)) {
        i18n.global.locale.value = data.language;
      }
      setGlobalRequestErrorAutoDismiss(data.development_mode ? null : 5000);
    }
  } catch {
    // silently fall back to default language
  }
}
