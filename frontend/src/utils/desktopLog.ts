/**
 * 桌面端日志适配：把前端 console 与全局错误转发到 Tauri 外壳日志。
 * 浏览器形态不安装，保持原生 console 行为；参考 `platform.ts` 的平台判断方式。
 *
 * 转发策略：
 * - warn / error / 全局异常始终转发，排查现场主要靠它们；
 * - log 默认不转发；启动时设置 TOGOSPACE_FRONTEND_DEBUG=1，或开发构建下设置
 *   localStorage['togospace.debug'] = '1' 后重新加载窗口开启；
 * - 启动关键节点（[boot]）始终转发，不受开关影响；
 * - 单条消息截断到 4000 字符，避免超长堆栈刷屏。
 */

import { isTauri } from './platform';

declare global {
  interface Window {
    /** Rust 通过 initialization_script 注入的运行时配置。 */
    tgconfig?: {
      /** 设置 TOGOSPACE_FRONTEND_DEBUG=1 时为 true，运行期修改也会即时生效。 */
      frontendDebug?: boolean;
    };
  }
}

/** 开发构建下临时打开 console.log 转发的 localStorage 开关。 */
const DEBUG_FLAG_KEY = 'togospace.debug';

/** 单条转发消息的最大长度。 */
const MAX_MESSAGE_LENGTH = 4000;

/** 安装 console 转发前保存原生实现，启动打点不会被重复转发。 */
const nativeConsoleLog = console.log.bind(console);

let installed = false;

/** 每次转发前动态读取，运行期修改 window.tgconfig 也即时生效。 */
function debugForwardingEnabled(): boolean {
  if (window.tgconfig?.frontendDebug === true) {
    return true;
  }
  try {
    return localStorage.getItem(DEBUG_FLAG_KEY) === '1';
  } catch {
    return false;
  }
}

function formatArg(arg: unknown): string {
  if (arg instanceof Error) {
    return arg.stack || `${arg.name}: ${arg.message}`;
  }
  return typeof arg === 'string' ? arg : String(arg);
}

function truncate(message: string): string {
  if (message.length <= MAX_MESSAGE_LENGTH) {
    return message;
  }
  const dropped = message.length - MAX_MESSAGE_LENGTH;
  return `${message.slice(0, MAX_MESSAGE_LENGTH)}… (truncated ${dropped} chars)`;
}

function forward(level: string, args: unknown[]): void {
  const message = truncate(`${level} ${args.map(formatArg).join(' ')}`);
  void import('@tauri-apps/api/core')
    .then(({ invoke }) => invoke('log_to_rust', { msg: message }))
    .catch(() => {
      // 外壳未就绪时忽略；原始日志已在浏览器控制台输出
    });
}

/** 安装 console 与全局错误转发，仅桌面形态生效，重复调用无副作用。 */
export function setupDesktopLogging(): void {
  if (installed || !isTauri()) {
    return;
  }
  installed = true;

  const originalWarn = console.warn.bind(console);
  const originalError = console.error.bind(console);

  console.warn = (...args: unknown[]) => {
    originalWarn(...args);
    forward('[console.warn]', args);
  };
  console.error = (...args: unknown[]) => {
    originalError(...args);
    forward('[console.error]', args);
  };

  const originalLog = console.log.bind(console);
  console.log = (...args: unknown[]) => {
    originalLog(...args);
    if (debugForwardingEnabled()) {
      forward('[console.log]', args);
    }
  };

  window.addEventListener('error', (event) => {
    forward('[window.error]', [
      event.message,
      `${event.filename}:${event.lineno}:${event.colno}`,
      event.error ?? '',
    ]);
  });
  window.addEventListener('unhandledrejection', (event) => {
    forward('[unhandledrejection]', [event.reason ?? '']);
  });
}

/** 记录首屏挂载结果，便于排查桌面端白屏；始终转发，不受 debug 开关影响。 */
export function logDesktopMountedState(): void {
  const root = document.getElementById('app');
  const args = [
    'mounted',
    `url=${location.href}`,
    `appChildren=${root?.children.length ?? 0}`,
    `textLength=${document.body.innerText.length}`,
    `debug=${debugForwardingEnabled()}`,
  ];
  nativeConsoleLog('[boot]', ...args);
  forward('[boot]', args);
}
