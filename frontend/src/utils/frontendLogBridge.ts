/**
 * 前端日志桥（#197）：把前端 console 日志经现有 ws 通道转发到后端，由后端写入本地文件。
 *
 * ## 与 `desktopLog.ts` 的分工（不重复、不互相替代）
 *
 * - `desktopLog.ts`：**仅桌面形态**，`warn`/`error`/全局异常恒定转发到 Tauri 外壳，
 *   落到 `{STORAGE_ROOT}/logs/desktop/desktop.log`。它解决的是「外壳与后端启动时序」
 *   这类只在桌面端有的问题。
 * - 本模块：**桌面与浏览器都装**，把日志经 ws 送到**后端**，落到
 *   `{STORAGE_ROOT}/logs/backend/frontend/frontend.log`。它解决的是 Operator 的原需求：
 *   「web 跑在浏览器里也要能转发」—— 浏览器形态没有 Tauri 外壳，`desktopLog.ts`
 *   在那一侧直接 return，等于完全没有落库能力。
 *
 * 桌面形态下 `warn`/`error` 会同时出现在两个文件里。这是刻意的：`desktop.log`
 * 记录「外壳视角看到了什么」，`frontend.log` 记录「后端收到了什么」，排查转发链路
 * 本身的问题时两者对比才有信息量。本模块**不修改 `desktopLog.ts` 的任何行为**。
 *
 * ## 开关
 *
 * 双层门控，两道都必须开：
 * 1. 前端侧（由 `setFrontendLogBridgeEnabled` 置位）：桌面来自 Rust 注入的
 *    `window.tgconfig.frontendDebug`，浏览器来自 `/system/status.json` 的
 *    `frontend_debug`。关着就连 ws 报文都不发（省流量）。
 * 2. 后端侧 `TOGOSPACE_FRONTEND_DEBUG`（`util/frontendLog.py`）：真正的写盘闸门。
 *    前端这道只是省流量，**不是**安全边界 —— 任何能连 ws 的客户端都能自己发报文。
 *
 * 前端侧有三态（`pending` / `enabled` / `disabled`）：未知态只攒队列、不发报文，
 * 拿到权威答案后再决定 flush 还是丢弃。
 *
 * ## 为什么不丢启动早期的日志
 *
 * ws 连上通常晚于首屏脚本执行，而排查白屏最需要的恰恰是最早那几行。所以未就绪时
 * 日志进环形队列（`MAX_QUEUE`），连接就绪后一次性 flush。
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
