import { watch } from 'vue';
import { connectionState, reconnectProgress, authEnabled, showTokenDialog } from '../appUiState';
import { createEventsSocket } from '../api';
import { getToken, clearToken } from '../authStore';
import { normalizeWsEventPayload, type FrontendRealtimeEvent } from './eventNormalizer';

type RealtimeListener = (event: FrontendRealtimeEvent) => void;

const reconnectDelayMs = 3000;
const connectTimeoutMs = 2000;

const listeners = new Set<RealtimeListener>();

/** 连接打开（鉴权已发出）时要通知的回调：前端日志桥据此 flush 积压。 */
const openListeners = new Set<() => void>();

let ws: WebSocket | null = null;
let reconnectTimer: number | null = null;
let reconnectCountdownTimer: number | null = null;
let connectTimeoutTimer: number | null = null;
let reconnectAttempt = 0;
let activeSocketToken = 0;
let started = false;
let socketOpen = false;
let authSent = false;

function clearReconnectCountdown(): void {
  reconnectProgress.value = 0;
  if (reconnectCountdownTimer !== null) {
    window.clearInterval(reconnectCountdownTimer);
    reconnectCountdownTimer = null;
  }
}

function clearConnectTimeout(): void {
  if (connectTimeoutTimer !== null) {
    window.clearTimeout(connectTimeoutTimer);
    connectTimeoutTimer = null;
  }
}

function startReconnectCountdown(delayMs: number): void {
  const reconnectAt = Date.now() + delayMs;

  const syncCountdown = (): void => {
    const remainingMs = reconnectAt - Date.now();
    const clampedRemaining = Math.min(Math.max(remainingMs, 0), delayMs);
    reconnectProgress.value = 1 - clampedRemaining / delayMs;
  };

  clearReconnectCountdown();
  syncCountdown();
  reconnectCountdownTimer = window.setInterval(syncCountdown, 50);
}

function emitRealtimeEvent(event: FrontendRealtimeEvent): void {
  for (const listener of listeners) {
    listener(event);
  }
}

/**
 * 把连接状态推进到 connected，并通知等待上行的订阅者。
 *
 * 汇总成一个函数是刻意的：`connected` 有三条到达路径（鉴权关闭时 open、
 * 收到 `auth_ok`、兼容旧后端的首条业务消息）。三处各写一遍「置状态 + 通知」
 * 迟早会漏一处，漏掉的那条就是「日志一直积压不发送」。
 */
function markConnected(): void {
  connectionState.value = 'connected';
  reconnectAttempt = 0;
  for (const listener of openListeners) {
    listener();
  }
}

/**
 * 发送一条上行报文（前端日志桥用）。
 *
 * 返回是否真的发出去了：连接未就绪时返回 false，调用方据此**保留**数据等下次 flush。
 * 静默丢弃是不可接受的 —— 排查白屏最需要的恰恰是启动最早那几行。
 *
 * 判据用 `connectionState === 'connected'` 而不是「socket 已 open」：鉴权开启时
 * `open` 之后还要等 `auth_ok`，这期间后端**会丢弃**未授权的上行。早发等于白发，
 * 而且从调用方看是「发出去了、日志却没了」，最难查。
 */
export function sendRealtimeUpstream(payload: Record<string, unknown>): boolean {
  if (!isRealtimeConnected() || !ws) {
    return false;
  }
  try {
    ws.send(JSON.stringify(payload));
    return true;
  } catch {
    return false;
  }
}

/** 订阅「连接已就绪（可发上行）」，返回取消函数。 */
export function onRealtimeConnected(listener: () => void): () => void {
  openListeners.add(listener);
  return () => {
    openListeners.delete(listener);
  };
}

/** 当前是否已就绪到可以发上行。 */
export function isRealtimeConnected(): boolean {
  return socketOpen && connectionState.value === 'connected';
}

function scheduleReconnect(): void {
  if (!started) {
    return;
  }
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
  }

  reconnectAttempt += 1;
  connectionState.value = 'waiting_reconnect';
  startReconnectCountdown(reconnectDelayMs);
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    clearReconnectCountdown();
    connectRealtimeSocket();
  }, reconnectDelayMs);
}

function sendAuthMessage(socket: WebSocket): void {
  if (authSent) {
    return;
  }

  const token = getToken();
  if (!token) {
    // 无 token，关闭连接并触发输入
    socket.close();
    ws = null;
    socketOpen = false;
    showTokenDialog.value = true;
    return;
  }

  authSent = true;
  socket.send(JSON.stringify({ type: 'auth', token }));
  // 认证已发送，连接状态保持 'connecting'，等待后端推送首条消息
}

function connectRealtimeSocket(): void {
  if (!started) {
    return;
  }

  activeSocketToken += 1;
  const socketToken = activeSocketToken;
  const isReconnectAttempt = reconnectAttempt > 0;

  clearConnectTimeout();

  if (ws) {
    ws.close();
    ws = null;
  }

  connectionState.value = isReconnectAttempt ? 'reconnecting' : 'connecting';
  clearReconnectCountdown();

  const socket = createEventsSocket();
  ws = socket;
  socketOpen = false;
  authSent = false;

  connectTimeoutTimer = window.setTimeout(() => {
    if (socketToken !== activeSocketToken || connectionState.value !== 'reconnecting') {
      return;
    }

    clearConnectTimeout();
    socket.close();
    ws = null;
    connectionState.value = 'disconnected';
    scheduleReconnect();
  }, connectTimeoutMs);

  socket.addEventListener('open', () => {
    if (socketToken !== activeSocketToken) {
      return;
    }

    clearConnectTimeout();
    socketOpen = true;

    if (authEnabled.value) {
      // 鉴权启用时，发送认证消息
      sendAuthMessage(socket);
      if (!authSent) {
        // 无 token，连接已关闭并弹出输入框
        return;
      }
    } else {
      // 鉴权未启用，直接标记为已连接
      markConnected();
    }

    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    clearReconnectCountdown();
  });

  socket.addEventListener('message', (messageEvent) => {
    if (socketToken !== activeSocketToken) {
      return;
    }

    try {
      const payload = JSON.parse(messageEvent.data);

      // 后端认证成功后显式回一帧 `{ event: 'auth_ok' }`，这是「连接就绪」的
      // 权威信号：不依赖总线上恰好有业务事件（否则右上角会一直显示「连接中」）。
      // 仍然兼容只发业务事件的旧后端——下面那条「首条消息即视为认证成功」保留，
      // 旧后端的首条业务消息照样能把状态置绿。
      const isAuthAck = authEnabled.value
        && (payload as { event?: unknown } | null)?.event === 'auth_ok';

      if (isAuthAck || (authEnabled.value && connectionState.value === 'connecting')) {
        markConnected();
      }

      if (isAuthAck) {
        // ack 只是连接层信号，没有业务含义，不进事件分发
        return;
      }

      const normalized = normalizeWsEventPayload(payload);
      if (normalized) {
        emitRealtimeEvent(normalized);
      }
    } catch (error) {
      console.error(error);
    }
  });

  socket.addEventListener('close', () => {
    if (socketToken !== activeSocketToken) {
      return;
    }

    ws = null;
    socketOpen = false;
    if (isReconnectAttempt && connectTimeoutTimer !== null && connectionState.value === 'reconnecting') {
      return;
    }

    clearConnectTimeout();
    connectionState.value = 'disconnected';
    scheduleReconnect();
  });

  socket.addEventListener('error', () => {
    if (socketToken !== activeSocketToken) {
      return;
    }

    socketOpen = false;
    if (isReconnectAttempt && connectTimeoutTimer !== null && connectionState.value === 'reconnecting') {
      return;
    }

    clearConnectTimeout();
    connectionState.value = 'disconnected';
  });
}

// 系统状态接口可能晚于 WebSocket 打开返回，连接会先按「无鉴权」处理。
// 鉴权随后开启时必须补发认证消息，否则后端一直等待认证、不订阅事件，
// 前端表现为连接正常但收不到任何实时推送。
watch(authEnabled, (enabled) => {
  if (!enabled || !socketOpen || !ws || authSent) {
    return;
  }

  connectionState.value = 'connecting';
  sendAuthMessage(ws);
});

export function startRealtimeClient(): void {
  if (started) {
    return;
  }
  started = true;
  connectRealtimeSocket();
}

export function stopRealtimeClient(): void {
  started = false;
  activeSocketToken += 1;

  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }

  clearConnectTimeout();
  clearReconnectCountdown();

  if (ws) {
    ws.close();
    ws = null;
  }

  socketOpen = false;
  authSent = false;
  reconnectAttempt = 0;
  connectionState.value = 'disconnected';
}

export function subscribeRealtimeEvents(listener: RealtimeListener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function __resetWsClientForTests(): void {
  started = false;
  activeSocketToken += 1;

  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  if (reconnectCountdownTimer !== null) {
    window.clearInterval(reconnectCountdownTimer);
    reconnectCountdownTimer = null;
  }
  if (connectTimeoutTimer !== null) {
    window.clearTimeout(connectTimeoutTimer);
    connectTimeoutTimer = null;
  }
  if (ws) {
    ws.close();
    ws = null;
  }

  listeners.clear();
  socketOpen = false;
  authSent = false;
  reconnectAttempt = 0;
  reconnectProgress.value = 0;
  connectionState.value = 'disconnected';
  showTokenDialog.value = false;
}
