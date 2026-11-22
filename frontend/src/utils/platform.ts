/**
 * 运行形态适配：桌面（Tauri）走原生能力，浏览器保持原有实现。
 */

declare global {
  interface Window {
    /** Rust 通过 initialization_script 注入的运行时配置。 */
    tgconfig?: {
      /** 设置 TOGOSPACE_FRONTEND_DEBUG=1 时为 true，运行期修改也会即时生效。 */
      frontendDebug?: boolean;
      /** macOS 使用透明覆盖式标题栏时为 true，页面需要为原生红绿灯预留顶部空间。 */
      overlayTitlebar?: boolean;
      /** Windows 使用无边框窗口、由前端自绘标题栏时为 true。 */
      windowsCustomTitlebar?: boolean;
    };
  }
}

export function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

/** 是否为 macOS 覆盖式标题栏（页面背景需要铺到窗口顶部）。 */
export function hasOverlayTitlebar(): boolean {
  return isTauri() && window.tgconfig?.overlayTitlebar === true;
}

/** 是否为 Windows 无边框自绘标题栏（页面需要为窗口按钮预留顶部高度）。 */
export function hasWindowsCustomTitlebar(): boolean {
  return isTauri() && window.tgconfig?.windowsCustomTitlebar === true;
}

/**
 * 把应用主题色同步到 Windows 无边框窗口的 DWM 属性。
 * 颜色直接从当前 CSS 变量读取，避免 Rust 侧重复维护一份主题色板。
 * 非 Windows 自绘标题栏为空操作。
 */
export function syncWindowsTitlebarTheme(mode: 'dark' | 'light'): void {
  if (!hasWindowsCustomTitlebar()) {
    return;
  }

  const styles = getComputedStyle(document.documentElement);
  const background = styles.getPropertyValue('--surface-page').trim();
  const foreground = styles.getPropertyValue('--text-primary').trim();
  if (!background || !foreground) {
    return;
  }

  void invokeTauri('sync_titlebar_theme', {
    background,
    foreground,
    dark: mode === 'dark',
  }).catch((error) => {
    console.warn('syncWindowsTitlebarTheme failed:', error);
  });
}

async function invokeTauri<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<T>(command, args);
}

/** 打开外链：桌面端交给系统浏览器，浏览器端新开标签页。 */
export async function openExternal(url: string): Promise<void> {
  if (isTauri()) {
    try {
      await invokeTauri('open_external', { url });
    } catch (error) {
      console.warn('openExternal failed:', error);
    }
    return;
  }
  window.open(url, '_blank', 'noopener,noreferrer');
}

/**
 * 保存文本文件：桌面端使用原生保存对话框，浏览器端沿用 Blob 下载。
 * 返回实际保存路径（浏览器形态返回文件名，取消时返回 null）。
 */
export async function saveTextFile(defaultName: string, content: string): Promise<string | null> {
  if (isTauri()) {
    return invokeTauri<string | null>('save_text_file', { defaultName, content });
  }

  const blob = new Blob([content], { type: 'application/json;charset=utf-8' });
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = defaultName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(objectUrl);
  return defaultName;
}
