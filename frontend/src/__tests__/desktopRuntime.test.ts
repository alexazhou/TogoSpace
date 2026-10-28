import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { clearToken, getToken, setToken } from '../authStore';
import { createEventsSocket, getSystemStatus, setApiBaseUrl } from '../api';
import { isTauri, openExternal } from '../utils/platform';

describe('authStore memory-first token', () => {
  beforeEach(() => {
    clearToken();
    localStorage.clear();
  });

  it('keeps non-persisted token in memory only', () => {
    setToken('desktop-token', { persist: false });

    expect(getToken()).toBe('desktop-token');
    expect(localStorage.getItem('teamagent_token')).toBeNull();
  });

  it('still persists tokens by default', () => {
    setToken('browser-token');

    expect(localStorage.getItem('teamagent_token')).toBe('browser-token');
  });

  it('clears both memory and localStorage', () => {
    setToken('browser-token');
    clearToken();

    expect(getToken()).toBeNull();
    expect(localStorage.getItem('teamagent_token')).toBeNull();
  });
});

describe('runtime API base url', () => {
  afterEach(() => {
    setApiBaseUrl('');
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('routes http requests through the runtime base url', async () => {
    setApiBaseUrl('http://127.0.0.1:4321');
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      initialized: true,
      language: 'zh-CN',
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await getSystemStatus();

    expect(fetchMock.mock.calls[0][0]).toBe('http://127.0.0.1:4321/system/status.json');
  });

  it('derives the websocket url from the runtime base url', () => {
    setApiBaseUrl('http://127.0.0.1:4321');
    const urls: string[] = [];

    class FakeWebSocket {
      constructor(url: string) {
        urls.push(url);
      }
    }
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);

    createEventsSocket();

    expect(urls).toEqual(['ws://127.0.0.1:4321/ws/events.json']);
  });
});

describe('platform utils in browser mode', () => {
  it('detects a non-tauri environment', () => {
    expect(isTauri()).toBe(false);
  });

  it('opens links in a new browser tab', async () => {
    const openMock = vi.spyOn(window, 'open').mockImplementation(() => null);

    await openExternal('https://example.com');

    expect(openMock).toHaveBeenCalledWith('https://example.com', '_blank', 'noopener,noreferrer');
    openMock.mockRestore();
  });
});
