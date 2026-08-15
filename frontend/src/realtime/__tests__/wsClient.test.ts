import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { authEnabled, connectionState, showTokenDialog } from '../../appUiState';

const {
  createEventsSocketMock,
  getTokenMock,
  normalizeWsEventPayloadMock,
} = vi.hoisted(() => ({
  createEventsSocketMock: vi.fn(),
  getTokenMock: vi.fn(),
  normalizeWsEventPayloadMock: vi.fn(),
}));

vi.mock('../../api', () => ({
  createEventsSocket: createEventsSocketMock,
}));

vi.mock('../../authStore', () => ({
  getToken: getTokenMock,
  clearToken: vi.fn(),
}));

vi.mock('../eventNormalizer', () => ({
  normalizeWsEventPayload: normalizeWsEventPayloadMock,
}));

import {
  __resetWsClientForTests,
  startRealtimeClient,
  stopRealtimeClient,
  subscribeRealtimeEvents,
} from '../wsClient';

type ListenerMap = {
  open: Array<() => void>;
  close: Array<() => void>;
  error: Array<() => void>;
  message: Array<(event: MessageEvent<string>) => void>;
};

class FakeWebSocket {
  public readonly listeners: ListenerMap = {
    open: [],
    close: [],
    error: [],
    message: [],
  };

  public readonly sentMessages: string[] = [];

  public closeCount = 0;

  addEventListener<K extends keyof ListenerMap>(type: K, listener: ListenerMap[K][number]): void {
    this.listeners[type].push(listener as never);
  }

  send(payload: string): void {
    this.sentMessages.push(payload);
  }

  close(): void {
    this.closeCount += 1;
    this.emit('close');
  }

  emit(type: 'open' | 'close' | 'error'): void;
  emit(type: 'message', payload: string): void;
  emit(type: keyof ListenerMap, payload?: string): void {
    if (type === 'message') {
      const event = { data: payload ?? '' } as MessageEvent<string>;
      this.listeners.message.forEach((listener) => listener(event));
      return;
    }

    this.listeners[type].forEach((listener) => listener());
  }
}

describe('wsClient', () => {
  const sockets: FakeWebSocket[] = [];

  beforeEach(() => {
    vi.useFakeTimers();
    sockets.length = 0;
    createEventsSocketMock.mockImplementation(() => {
      const socket = new FakeWebSocket();
      sockets.push(socket);
      return socket;
    });
    getTokenMock.mockReset();
    normalizeWsEventPayloadMock.mockReset();
    authEnabled.value = false;
    __resetWsClientForTests();
  });

  afterEach(() => {
    stopRealtimeClient();
    __resetWsClientForTests();
    vi.useRealTimers();
  });

  it('connects immediately when auth is disabled', () => {
    startRealtimeClient();

    expect(connectionState.value).toBe('connecting');
    expect(sockets).toHaveLength(1);

    sockets[0]?.emit('open');

    expect(connectionState.value).toBe('connected');
  });

  it('authenticates with token and emits normalized realtime events', () => {
    authEnabled.value = true;
    getTokenMock.mockReturnValue('secret-token');
    const listener = vi.fn();
    const normalizedEvent = { type: 'schedule_state', scheduleState: 'running', notRunningReason: '' };
    normalizeWsEventPayloadMock.mockReturnValue(normalizedEvent);

    const unsubscribe = subscribeRealtimeEvents(listener);
    startRealtimeClient();
    sockets[0]?.emit('open');

    expect(connectionState.value).toBe('connecting');
    expect(sockets[0]?.sentMessages).toEqual([
      JSON.stringify({ type: 'auth', token: 'secret-token' }),
    ]);

    sockets[0]?.emit('message', JSON.stringify({ event: 'schedule_state' }));

    expect(connectionState.value).toBe('connected');
    expect(listener).toHaveBeenCalledWith(normalizedEvent);

    unsubscribe();
  });

  it('shows token dialog when auth is enabled but no token is available', () => {
    authEnabled.value = true;
    getTokenMock.mockReturnValue(null);

    startRealtimeClient();
    sockets[0]?.emit('open');

    expect(showTokenDialog.value).toBe(true);
    expect(sockets[0]?.closeCount).toBe(1);
  });

  it('schedules reconnects after socket close', () => {
    startRealtimeClient();
    sockets[0]?.emit('open');
    sockets[0]?.emit('close');

    expect(connectionState.value).toBe('waiting_reconnect');

    vi.advanceTimersByTime(3000);

    expect(sockets).toHaveLength(2);
    expect(connectionState.value).toBe('reconnecting');

    sockets[1]?.emit('open');

    expect(connectionState.value).toBe('connected');
  });

  it('cancels pending reconnects when stopped', () => {
    startRealtimeClient();
    sockets[0]?.emit('open');
    sockets[0]?.emit('close');

    expect(connectionState.value).toBe('waiting_reconnect');

    stopRealtimeClient();
    vi.advanceTimersByTime(3000);

    expect(sockets).toHaveLength(1);
    expect(connectionState.value).toBe('disconnected');
  });
});
