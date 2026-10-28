/**
 * 运行形态适配：桌面（Tauri）走原生能力，浏览器保持原有实现。
 */

export function isTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
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
