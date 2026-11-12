/**
 * 前端日志桥（#197）单测。
 *
 * wsClient 用 `vi.mock` 换成可控的假实现：这里要验的是「桥在什么条件下发、发什么、
 * 发不出去怎么办」，与真实 ws 的建连过程无关（那部分在 `wsClient.test.ts` 里）。
 *
 * 假实现在**返回 true 时才记入 `delivered`** —— 直接数 mock 调用次数是错的：
 * 一次「尝试发送但失败」也会留下调用记录，于是「队列里还压着」和「已经发出去了」
 * 看起来一样。这个区别正是本模块全部行为的分界。
 */

import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

const { sendRealtimeUpstreamMock, isRealtimeConnectedMock, onRealtimeConnectedMock } = vi.hoisted(
  () => ({
    sendRealtimeUpstreamMock: vi.fn(),
    isRealtimeConnectedMock: vi.fn(),
    onRealtimeConnectedMock: vi.fn(),
  }),
);

vi.mock('../realtime/wsClient', () => ({
  sendRealtimeUpstream: sendRealtimeUpstreamMock,
  isRealtimeConnected: isRealtimeConnectedMock,
  onRealtimeConnected: onRealtimeConnectedMock,
}));

const originalConsole = {
  log: console.log,
  warn: console.warn,
  error: console.error,
};

/** 连接是否就绪：置 false 模拟「ws 还没连上 / 还没认证完」。 */
let connectionReady = true;
/** 真正发出去的报文（只有 sendRealtimeUpstream 返回 true 的才在这里）。 */
let delivered: Array<Record<string, unknown>> = [];
/** 让下一次 sendRealtimeUpstream 失败（模拟发送中途断链）。 */
let failNextSends = 0;
/** 让正文含该子串的那条发送失败（用于验「停在失败处」而非「跳过」）。 */
let failOnMessage: string | null = null;
/** `onRealtimeConnected` 注册的回调，测试里手动触发。 */
let connectedListeners: Array<() => void> = [];

async function loadBridge(): Promise<typeof import('../utils/frontendLogBridge')> {
  vi.resetModules();
  return import('../utils/frontendLogBridge');
}

function deliveredMessages(): string[] {
  return delivered.map((payload) => payload.message as string);
}

describe('frontendLogBridge', () => {
  beforeEach(() => {
    connectionReady = true;
    delivered = [];
    failNextSends = 0;
    failOnMessage = null;
    connectedListeners = [];

    sendRealtimeUpstreamMock.mockReset();
    isRealtimeConnectedMock.mockReset();
    onRealtimeConnectedMock.mockReset();

    sendRealtimeUpstreamMock.mockImplementation((payload: Record<string, unknown>) => {
      const failing = !connectionReady || failNextSends > 0 || failOnMessage === payload.message;
      if (!connectionReady || failNextSends > 0) {
        if (failNextSends > 0) {
          failNextSends -= 1;
        }
      }
      if (failing) {
        return false;
      }
      delivered.push(payload);
      return true;
    });
    isRealtimeConnectedMock.mockImplementation(() => connectionReady);
    onRealtimeConnectedMock.mockImplementation((listener: () => void) => {
      connectedListeners.push(listener);
      return () => {
        connectedListeners = connectedListeners.filter((item) => item !== listener);
      };
    });

    // 装钩子后本来就会调用原生实现，压掉避免刷屏
    console.log = vi.fn();
    console.warn = vi.fn();
    console.error = vi.fn();
  });

  afterEach(() => {
    console.log = originalConsole.log;
    console.warn = originalConsole.warn;
    console.error = originalConsole.error;
    vi.restoreAllMocks();
  });

  /** 装好桥并把开关置为确定态（`null` = 保持未知）。 */
  async function boot(
    switchState: boolean | null,
  ): Promise<typeof import('../utils/frontendLogBridge')> {
    const bridge = await loadBridge();
    bridge.setupFrontendLogBridge();
    if (switchState !== null) {
      bridge.setFrontendLogBridgeEnabled(switchState);
    }
    return bridge;
  }

  // -------------------------------------------------------------------------
  // 三态门控
  // -------------------------------------------------------------------------

  it('does not forward anything while the switch is still unknown', async () => {
    // 开关要等后端状态返回才确定。此前既不能发报文（发出去等于赌），
    // 也不能当成已关闭（那会丢掉启动早期日志）。
    await boot(null);

    console.warn('before-status');

    expect(deliveredMessages()).toEqual([]);
  });

  it('flushes the startup backlog once the switch turns out to be on', async () => {
    // 白屏排查最需要的就是启动最早那几行，它们必然早于后端状态返回。
    const bridge = await boot(null);
    console.log('boot-line-1');
    console.error('boot-line-2');

    bridge.setFrontendLogBridgeEnabled(true);

    expect(deliveredMessages()).toEqual(['boot-line-1', 'boot-line-2']);
  });

  it('drops the backlog when the switch turns out to be off', async () => {
    // 交付标准 2：开关关闭时不做任何额外后端请求。未知态攒下的队列必须清掉，
    // 否则「先攒后发」会变成一次迟到的额外请求。
    const bridge = await boot(null);
    console.log('boot-line');
    sendRealtimeUpstreamMock.mockClear();

    bridge.setFrontendLogBridgeEnabled(false);
    console.log('after-off');

    expect(deliveredMessages()).toEqual([]);
    expect(sendRealtimeUpstreamMock).not.toHaveBeenCalled();
  });

  it('forwards console output with level and ts after the switch is on', async () => {
    await boot(true);

    console.warn('careful', 42);

    const payload = delivered[0];
    expect(payload).toMatchObject({ type: 'frontend_log', level: 'warn', message: 'careful 42' });
    expect(typeof payload.ts).toBe('number');
  });

  // -------------------------------------------------------------------------
  // 队列
  // -------------------------------------------------------------------------

  it('queues in order when the socket is not ready and flushes later', async () => {
    const bridge = await boot(true);

    connectionReady = false;
    console.log('queued-1');
    console.log('queued-2');
    expect(deliveredMessages()).toEqual([]);

    connectionReady = true;
    expect(bridge.flushFrontendLogQueue()).toBe(2);
    expect(deliveredMessages()).toEqual(['queued-1', 'queued-2']);
    // 发完即清空，重复 flush 不重发
    expect(bridge.flushFrontendLogQueue()).toBe(0);
  });

  it('stops flushing at the first failure to preserve order', async () => {
    // 跳条会把时间顺序毁掉，而顺序本身是判据。失败就停，剩下的留着下次。
    const bridge = await boot(true);

    connectionReady = false;
    console.log('a');
    console.log('b');
    console.log('c');

    // 只让中间那条失败：验证「停在失败处」而不是「跳过它继续发后面的」
    connectionReady = true;
    failOnMessage = 'b';
    expect(bridge.flushFrontendLogQueue()).toBe(1);
    expect(deliveredMessages()).toEqual(['a']);

    failOnMessage = null;
    expect(bridge.flushFrontendLogQueue()).toBe(2);
    expect(deliveredMessages()).toEqual(['a', 'b', 'c']);
  });

  it('keeps only the newest entries when the queue overflows', async () => {
    // 卡死时最有价值的是「最后发生了什么」，所以满了丢最旧。
    const bridge = await boot(true);

    connectionReady = false;
    for (let index = 0; index < 520; index += 1) {
      console.log(`entry-${index}`);
    }

    connectionReady = true;
    expect(bridge.flushFrontendLogQueue()).toBe(500);

    const messages = deliveredMessages();
    expect(messages).toHaveLength(500);
    expect(messages[0]).toBe('entry-20');
    expect(messages[messages.length - 1]).toBe('entry-519');
  });

  // -------------------------------------------------------------------------
  // 内容整形
  // -------------------------------------------------------------------------

  it('truncates very long messages', async () => {
    await boot(true);

    console.log('x'.repeat(5000));

    const message = deliveredMessages()[0];
    expect(message.length).toBeLessThan(5000);
    expect(message).toContain('truncated 1000 chars');
  });

  it('formats errors with their stack so the origin is not lost', async () => {
    await boot(true);

    console.error(new Error('boom'));

    expect(deliveredMessages()[0]).toContain('Error: boom');
    expect(deliveredMessages()[0]).toContain('frontendLogBridge.test.ts');
  });

  it('serializes plain objects as JSON', async () => {
    // 用 JSON 而不是 `String(obj)`：要与 devtools 里看到的形状一致，
    // 否则落盘后会像「前端发了这种东西」一样误导排查。
    await boot(true);

    console.log({ a: 1, b: 'two' });

    expect(deliveredMessages()[0]).toBe('{"a":1,"b":"two"}');
  });

  it('does not recurse when the message itself is a stringified log line', async () => {
    // 桥自己的原生调用不经过 enqueue，所以一条日志只会产生一条报文。
    await boot(true);

    console.log('single');
    console.log('single-2');

    expect(deliveredMessages()).toEqual(['single', 'single-2']);
  });

  // -------------------------------------------------------------------------
  // 安装行为
  // -------------------------------------------------------------------------

  it('installs the console hooks even before the switch is known', async () => {
    // 等开关再装会永久丢掉启动早期日志 —— 而那正是白屏问题的现场。
    const bridge = await boot(null);
    const patchedLog = console.log;
    const patchedWarn = console.warn;
    const patchedError = console.error;

    bridge.setupFrontendLogBridge(); // 幂等

    expect(console.log).toBe(patchedLog);
    expect(console.warn).toBe(patchedWarn);
    expect(console.error).toBe(patchedError);
  });

  it('always calls the native console so devtools output is unchanged', async () => {
    // 原生实现在**模块加载时**就捕获了，所以要在 loadBridge 之前换掉。
    const nativeMock = vi.fn();
    console.log = nativeMock;
    const bridge = await loadBridge();
    bridge.setupFrontendLogBridge();

    console.log('visible-in-devtools');

    expect(nativeMock).toHaveBeenCalledWith('visible-in-devtools');
  });

  it('flushes automatically when the realtime connection becomes ready', async () => {
    const bridge = await boot(true);

    connectionReady = false;
    console.log('buffered');
    expect(deliveredMessages()).toEqual([]);

    connectionReady = true;
    connectedListeners.forEach((listener) => listener());

    expect(deliveredMessages()).toEqual(['buffered']);
  });

  // -------------------------------------------------------------------------
  // 桌面运行时配置
  // -------------------------------------------------------------------------

  it('reads the desktop runtime flag from window.tgconfig', async () => {
    const bridge = await loadBridge();

    expect(bridge.frontendLogBridgeEnabledFromRuntimeConfig()).toBeNull();

    window.tgconfig = { frontendDebug: true };
    expect(bridge.frontendLogBridgeEnabledFromRuntimeConfig()).toBe(true);

    window.tgconfig = { frontendDebug: false };
    expect(bridge.frontendLogBridgeEnabledFromRuntimeConfig()).toBe(false);

    delete window.tgconfig;
  });
});
