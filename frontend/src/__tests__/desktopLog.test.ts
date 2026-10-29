import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { invokeMock } = vi.hoisted(() => ({ invokeMock: vi.fn().mockResolvedValue(undefined) }));

vi.mock('@tauri-apps/api/core', () => ({ invoke: invokeMock }));

const originalConsole = {
  log: console.log,
  warn: console.warn,
  error: console.error,
};

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 20));
}

interface DesktopLogOptions {
  /** 通过 localStorage 打开 console.log 转发 */
  local?: boolean;
  /** Rust initialization_script 注入的 window.tgconfig.frontendDebug */
  injected?: boolean;
}

async function importDesktopLog(
  options: DesktopLogOptions = {},
): Promise<typeof import('../utils/desktopLog')> {
  vi.resetModules();
  (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  localStorage.clear();
  if (options.local) {
    localStorage.setItem('togospace.debug', '1');
  }
  if (options.injected) {
    window.tgconfig = { frontendDebug: true };
  }
  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
  return import('../utils/desktopLog');
}

function forwardedMessages(): string[] {
  return invokeMock.mock.calls
    .filter(([command]) => command === 'log_to_rust')
    .map(([, payload]) => (payload as { msg: string }).msg);
}

describe('desktop log forwarding', () => {
  beforeEach(() => {
    invokeMock.mockClear();
  });

  afterEach(() => {
    console.log = originalConsole.log;
    console.warn = originalConsole.warn;
    console.error = originalConsole.error;
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    delete window.tgconfig;
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('forwards warn and error but drops log by default', async () => {
    const { setupDesktopLogging } = await importDesktopLog();
    setupDesktopLogging();

    // 逐个调用并等待：Vitest 对并发动态 import 的 mock 存在取值不稳定问题，
    // 真实运行时（非 mocked ESM）不存在该限制。
    console.warn('careful');
    await flush();
    console.error('boom');
    await flush();
    console.log('noise');
    await flush();

    const messages = forwardedMessages();
    expect(messages).toContain('[console.warn] careful');
    expect(messages).toContain('[console.error] boom');
    expect(messages.some((message) => message.startsWith('[console.log]'))).toBe(false);
  });

  it('forwards log when the localStorage debug flag is enabled', async () => {
    const { setupDesktopLogging } = await importDesktopLog({ local: true });
    setupDesktopLogging();

    console.log('noise');
    await flush();

    expect(forwardedMessages()).toContain('[console.log] noise');
  });

  it('forwards log when window.tgconfig enables debug', async () => {
    const { setupDesktopLogging } = await importDesktopLog({ injected: true });
    setupDesktopLogging();

    console.log('noise');
    await flush();

    expect(forwardedMessages()).toContain('[console.log] noise');
  });

  it('reads the flag dynamically at each log call', async () => {
    const { setupDesktopLogging } = await importDesktopLog();
    setupDesktopLogging();

    console.log('before');
    await flush();
    expect(forwardedMessages()).not.toContain('[console.log] before');

    window.tgconfig = { frontendDebug: true };
    console.log('after');
    await flush();
    expect(forwardedMessages()).toContain('[console.log] after');
  });

  it('always forwards the boot marker even with debug off', async () => {
    const { setupDesktopLogging, logDesktopMountedState } = await importDesktopLog();
    setupDesktopLogging();

    logDesktopMountedState();
    await flush();

    expect(forwardedMessages().some((message) => message.startsWith('[boot] mounted'))).toBe(true);
  });

  it('truncates oversized messages', async () => {
    const { setupDesktopLogging } = await importDesktopLog({ local: true });
    setupDesktopLogging();

    console.log('x'.repeat(5000));
    await flush();

    const message = forwardedMessages().find((item) => item.startsWith('[console.log]'));
    expect(message).toBeDefined();
    expect(message).toContain('(truncated');
    expect(message?.length).toBeLessThan(4100);
  });
});
