import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

const STARTING_TEXT = '正在启动中…';
const MIGRATING_TEXT = '数据库升级中，请等待，不要关闭程序';

const splashStartedAt = performance.now();

function splashLog(message: string): void {
  const elapsedMs = Math.round(performance.now() - splashStartedAt);
  const line = `[splash +${elapsedMs}ms] ${message}`;
  console.log(line);
  invoke('log_to_rust', { msg: line }).catch(() => {});
}

function formatError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  if (typeof error === 'string') {
    return error;
  }
  try {
    const serialized = JSON.stringify(error);
    if (serialized) {
      return serialized;
    }
  } catch {
    // fall through
  }
  return String(error);
}

async function initSplash(): Promise<void> {
  const loadingState = document.getElementById('loadingState')!;
  const errorState = document.getElementById('errorState')!;
  const loadingText = document.getElementById('loadingText')!;
  const errorText = document.getElementById('errorText')!;
  const btnExit = document.getElementById('btnExit')!;

  let hasError = false;

  const showError = (message: string): void => {
    hasError = true;
    splashLog(`show error: ${message}`);
    loadingState.style.display = 'none';
    errorState.style.display = 'flex';
    errorText.innerText = message;
  };

  btnExit.addEventListener('click', () => {
    void invoke('exit_app');
  });

  await Promise.all([
    listen('backend-migration-start', () => {
      splashLog('backend migration started');
      loadingText.innerText = MIGRATING_TEXT;
    }),
    listen('backend-migration-end', () => {
      splashLog('backend migration ended');
      loadingText.innerText = STARTING_TEXT;
    }),
    listen('backend-error', (event) => {
      splashLog(`backend-error received: ${formatError(event.payload)}`);
      showError(formatError(event.payload));
    }),
  ]);

  splashLog('splash event listeners registered');

  // 兜底：重试场景下错误事件可能在监听注册前就已发出
  if (!hasError) {
    const state = await invoke<string>('get_backend_state');
    if (state === 'failed' || state === 'exited') {
      const message = await invoke<string | null>('get_backend_error');
      showError(message || '后端未能启动');
    }
  }
}

initSplash().catch((error) => {
  console.error(error);
});
