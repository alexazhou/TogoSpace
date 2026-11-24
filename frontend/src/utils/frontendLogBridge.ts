/** 把前端 console 日志经 ws 送到后端，由后端写进 `{STORAGE_ROOT}/logs/backend/frontend/frontend.log`。
 * 现象：浏览器形态下 `desktopLog.ts` 直接 return，那一侧完全没有落库能力；ws 又比首屏脚本晚连上，排查白屏最需要的最早几行会丢。
 * 原因：桌面和浏览器都要装，连上之前先排进队列 `MAX_QUEUE`；前端开关只用来省流量，真正决定写不写盘的是后端的 `TOGOSPACE_FRONTEND_DEBUG`。
 * 要改这里：别让日志绕过 `MAX_QUEUE` 直接发，也别改 `desktopLog.ts` 的行为；两道开关与三态见 `docs/tech/08_debugging/frontend_log_bridge.md`。
 */

import {
  isRealtimeConnected,
  onRealtimeConnected,
  sendRealtimeUpstream,
} from '../realtime/wsClient';

/** 单条正文最大长度。与 `desktopLog.ts` 取同一个值，口径一致；后端还会再截一次。 */
const MAX_MESSAGE_LENGTH = 4000;

/** 未就绪时的积压上限。超出丢最旧的 —— 卡死时最需要的是「最后发生了什么」。 */
const MAX_QUEUE = 500;

export type FrontendLogLevel = 'log' | 'info' | 'warn' | 'error' | 'debug';

export interface FrontendLogEntry {
  level: FrontendLogLevel;
  message: string;
  ts: number;
}

const queue: FrontendLogEntry[] = [];

/**
 * 桥的三态。
 *
 * `pending` 是必需的第三态，不能退化成布尔：开关要在后端 `/system/status.json`
 * 返回后才确定，而最先跑完的恰恰是启动早期脚本。若「未知」直接当成「关」，
 * 白屏排查最需要的前几行就永久丢了；若当成「关但先攒着」，又会在开关其实
 * 关闭时白攒一堆。所以未知态只进内存队列、**不发任何报文**，拿到状态后再决定
 * 发出还是丢弃 —— 交付标准里的「开关关闭 ⇒ 无额外后端请求」说的是网络行为，
 * 内存里的临时缓冲不违反它。
 */
type BridgeState = 'pending' | 'enabled' | 'disabled';

let installed = false;
let state: BridgeState = 'pending';

/** 原生 console，装在桥之前；避免自转发与递归。 */
const nativeConsole = {
  log: console.log.bind(console),
  warn: console.warn.bind(console),
  error: console.error.bind(console),
};

function truncate(message: string): string {
  if (message.length <= MAX_MESSAGE_LENGTH) {
    return message;
  }
  const dropped = message.length - MAX_MESSAGE_LENGTH;
  return `${message.slice(0, MAX_MESSAGE_LENGTH)}… (truncated ${dropped} chars)`;
}

function formatArg(arg: unknown): string {
  if (arg instanceof Error) {
    return arg.stack || `${arg.name}: ${arg.message}`;
  }
  if (typeof arg === 'string') {
    return arg;
  }
  if (typeof arg === 'object' && arg !== null) {
    try {
      // 与 devtools 里看到的一致，避免 Python `repr` 那样的非 JS 形状
      return JSON.stringify(arg);
    } catch {
      return String(arg);
    }
  }
  return String(arg);
}

function enqueue(level: FrontendLogLevel, args: unknown[]): void {
  if (state === 'disabled') {
    return;
  }
  const entry: FrontendLogEntry = {
    level,
    message: truncate(args.map(formatArg).join(' ')),
    ts: Date.now(),
  };

  // 未知态一律只进队列：此刻法定的答案还没回来，发出去等于赌，攒着等通知。
  if (state === 'enabled' && sendRealtimeUpstream(toPayload(entry))) {
    return;
  }

  queue.push(entry);
  if (queue.length > MAX_QUEUE) {
    queue.splice(0, queue.length - MAX_QUEUE);
  }
}

function toPayload(entry: FrontendLogEntry): Record<string, unknown> {
  return {
    type: 'frontend_log',
    level: entry.level,
    message: entry.message,
    ts: entry.ts,
  };
}

/**
 * 把积压的日志按顺序发出去；遇到发不出去就停下（保持顺序，不跳条）。
 *
 * 停下而不是跳过是刻意的：日志的时间顺序本身是判据，跳着发反而会误导排查。
 */
export function flushFrontendLogQueue(): number {
  if (state !== 'enabled' || !isRealtimeConnected()) {
    return 0;
  }
  let sent = 0;
  while (queue.length > 0) {
    const entry = queue[0];
    if (!sendRealtimeUpstream(toPayload(entry))) {
      break;
    }
    queue.shift();
    sent += 1;
  }
  return sent;
}

/**
 * 开关前端侧门控。`null` 表示「还没拿到权威答案」，回到未知态（保留队列）。
 *
 * 关掉时清空积压：关着还留着旧日志，开的时候会发一堆过期数据。
 */
export function setFrontendLogBridgeEnabled(value: boolean | null): void {
  if (value === null) {
    state = 'pending';
    return;
  }
  const next: BridgeState = value === true ? 'enabled' : 'disabled';
  if (next === state) {
    return;
  }
  state = next;
  if (state === 'disabled') {
    queue.length = 0;
    return;
  }
  // 打开时可能已经连上了（后端状态通常晚于 ws 就绪）
  flushFrontendLogQueue();
}

export function isFrontendLogBridgeEnabled(): boolean {
  return state === 'enabled';
}

/** 仅供测试：重置模块状态。 */
export function __resetFrontendLogBridgeForTests(): void {
  queue.length = 0;
  state = 'pending';
  installed = false;
}

/**
 * 桌面形态的开关来源：Rust 在页面脚本执行前注入的 `window.tgconfig.frontendDebug`。
 *
 * 桌面端后端状态和 ws 都可能晚到，而这条标记在首行脚本前就有了 —— 用它先把
 * 「未知」推进到确定态，避免桌面形态下每次都要等 `/system/status.json`。
 * 只认显式 `true`，与 `desktopLog.ts` 同口径。
 */
export function frontendLogBridgeEnabledFromRuntimeConfig(): boolean | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const flag = window.tgconfig?.frontendDebug;
  if (flag === true) {
    return true;
  }
  if (flag === false) {
    return false;
  }
  // undefined：浏览器形态，或桌面注入失败 —— 交给后端状态决定
  return null;
}

/**
 * 安装 console 钩子。重复调用无副作用。
 *
 * **无论有没有打开开关都装**：开关是在启动后才从后端读到的，如果等开关再装，
 * 从脚本执行到拿到状态这段时间的日志就永久丢了 —— 而那正是白屏问题的现场。
 * 关闭时 `enqueue` 首行就 return，开销只是一次枚举比较。
 */
export function setupFrontendLogBridge(): void {
  if (installed) {
    return;
  }
  installed = true;

  console.log = (...args: unknown[]) => {
    nativeConsole.log(...args);
    enqueue('log', args);
  };
  console.warn = (...args: unknown[]) => {
    nativeConsole.warn(...args);
    enqueue('warn', args);
  };
  console.error = (...args: unknown[]) => {
    nativeConsole.error(...args);
    enqueue('error', args);
  };

  if (typeof window !== 'undefined') {
    window.addEventListener('error', (event) => {
      enqueue('error', [
        '[window.error]',
        event.message,
        `${event.filename}:${event.lineno}:${event.colno}`,
        event.error ?? '',
      ]);
    });
    window.addEventListener('unhandledrejection', (event) => {
      enqueue('error', ['[unhandledrejection]', event.reason ?? '']);
    });
    // 连接就绪即补发积压
    onRealtimeConnected(() => {
      flushFrontendLogQueue();
    });
  }
}
