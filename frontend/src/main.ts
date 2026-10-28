import { createApp } from 'vue';
import App from './App.vue';
import router from './router';
import i18n, { syncLanguageFromBackend } from './i18n';
import { setApiBaseUrl } from './api';
import { setToken } from './authStore';
import { isTauri } from './utils/platform';
import '@fortawesome/fontawesome-free/css/fontawesome.min.css';
import '@fortawesome/fontawesome-free/css/solid.min.css';
import './theme/tokens.css';
import './style.css';

/** 桌面模式：挂载前注入后端地址与内存态 token。 */
async function loadDesktopRuntime(): Promise<void> {
  const { invoke } = await import('@tauri-apps/api/core');
  const [url, token] = await Promise.all([
    invoke<string>('get_backend_url'),
    invoke<string>('get_auth_token'),
  ]);
  if (url) {
    setApiBaseUrl(url);
  }
  if (token) {
    setToken(token, { persist: false });
  }
}

/** 前端 console.log 转发到外壳日志，便于对齐启动时间线。 */
function forwardConsoleToRust(): void {
  const originalLog = console.log.bind(console);
  console.log = (...args: unknown[]) => {
    originalLog(...args);
    void import('@tauri-apps/api/core')
      .then(({ invoke }) => invoke('log_to_rust', { msg: args.map(String).join(' ') }))
      .catch(() => {});
  };
}

async function bootstrap(): Promise<void> {
  if (isTauri()) {
    forwardConsoleToRust();
    try {
      await loadDesktopRuntime();
    } catch (error) {
      console.error('Failed to load desktop runtime config:', error);
    }
  }

  // 必须在 base 注入之后：内部会请求 /system/status.json
  await syncLanguageFromBackend();
  createApp(App).use(router).use(i18n).mount('#app');
}

void bootstrap();
