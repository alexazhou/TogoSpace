import { watch } from 'vue';
import { connectionState, reconnectProgress, authEnabled, showTokenDialog } from '../appUiState';
import { createEventsSocket } from '../api';
import { getToken, clearToken } from '../authStore';
import { normalizeWsEventPayload, type FrontendRealtimeEvent } from './eventNormalizer';

type RealtimeListener = (event: FrontendRealtimeEvent) => void;

const reconnectDelayMs = 3000;
const connectTimeoutMs = 2000;

const listeners = new Set<RealtimeListener>();

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
      connectionState.value = 'connected';
      reconnectAttempt = 0;
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

    // 鉴权启用时，第一条消息到达表示认证成功
    if (authEnabled.value && connectionState.value === 'connecting') {
      connectionState.value = 'connected';
      reconnectAttempt = 0;
    }

    try {
      const payload = JSON.parse(messageEvent.data);
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
