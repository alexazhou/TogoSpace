import { flushPromises, mount } from '@vue/test-utils';
import { createI18n } from 'vue-i18n';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * #339：聊天框拖入文件的两种运行形态。
 *
 * - 浏览器形态：只拿得到 `DataTransfer.files[i].name`（**拿不到绝对路径**），必须
 *   `preventDefault()` 阻止整页跳去打开文件。
 * - 桌面（Tauri）形态：DOM 层没有真实文件路径，插入交给 `onDragDropEvent` 的 `payload.paths`；
 *   同期 DOM `drop` 只兜底 `preventDefault`，**不得插入**（否则会与事件通道双插）。
 *
 * - 落点判定（#340）：桌面端是 webview 级事件，必须自己用 `payload.position`（物理像素）
 *   与输入框 rect（CSS 像素）按 DPR 比较；未命中 / 拿不到 position ⇒ 什么都不做（fail-closed）。
 *
 * 这里用 `vi.mock` 替掉 `@tauri-apps/api/webview`，并把注册到的 handler 抓出来手摇，
 * 因此断言的是「我们自己的接线」，不依赖真实 WKWebView。
 */
type DragDropPayload = {
  type: string;
  paths?: string[];
  position?: { x: number; y: number };
};
type DropPayloadHandler = (event: { payload: DragDropPayload }) => void;

const { dragDropHandlers, unlistenMock } = vi.hoisted(() => ({
  dragDropHandlers: [] as Array<DropPayloadHandler>,
  unlistenMock: vi.fn(),
}));

vi.mock('@tauri-apps/api/webview', () => ({
  getCurrentWebview: () => ({
    onDragDropEvent: (handler: DropPayloadHandler) => {
      dragDropHandlers.push(handler);
      return Promise.resolve(unlistenMock);
    },
  }),
}));

import ChatComposer from '../ChatComposer.vue';

function mountComposer(draft = '') {
  const i18n = createI18n({
    legacy: false,
    locale: 'zh',
    missingWarn: false,
    fallbackWarn: false,
    messages: {
      zh: { chat: { inputPlaceholder: '输入消息', sendHint: 'Enter 发送', send: '发送' } },
    },
  });

  return mount(ChatComposer, {
    props: { draft, dragging: false, composerStyle: {}, notice: '' },
    global: { plugins: [i18n] },
  });
}

function makeDragEvent(type: string, files: Array<{ name: string }> = []): DragEvent {
  const event = new Event(type, { bubbles: true, cancelable: true }) as DragEvent;
  Object.defineProperty(event, 'dataTransfer', {
    value: { files, types: files.length > 0 ? ['Files'] : ['text/plain'] },
  });
  return event;
}

function lastDraft(wrapper: ReturnType<typeof mountComposer>): string | undefined {
  const emitted = wrapper.emitted('updateDraft') ?? [];
  return emitted.length > 0 ? (emitted[emitted.length - 1][0] as string) : undefined;
}

/** 输入框在页面里的 CSS 像素矩形（测试里手动 stub，happy-dom 没有排版）。 */
const EDITOR_RECT = { left: 10, top: 20, width: 300, height: 120 };
/** 落在 EDITOR_RECT 内的物理坐标（DPR = 1）。 */
const INSIDE = { x: 100, y: 100 };
/** 落在 EDITOR_RECT 外的物理坐标（DPR = 1）：侧边栏区域。 */
const OUTSIDE = { x: 900, y: 600 };

function stubEditorRect(wrapper: ReturnType<typeof mountComposer>): void {
  const editor = wrapper.find('.composer-editor').element as HTMLElement;
  editor.getBoundingClientRect = () =>
    ({
      ...EDITOR_RECT,
      x: EDITOR_RECT.left,
      y: EDITOR_RECT.top,
      right: EDITOR_RECT.left + EDITOR_RECT.width,
      bottom: EDITOR_RECT.top + EDITOR_RECT.height,
      toJSON: () => EDITOR_RECT,
    }) as DOMRect;
}

function dropEvent(paths: string[], position?: { x: number; y: number }): { payload: DragDropPayload } {
  return { payload: { type: 'drop', paths, position } };
}

function enterTauriRuntime() {
  Object.defineProperty(window, '__TAURI_INTERNALS__', { value: {}, configurable: true });
}

function setDevicePixelRatio(value: number): void {
  Object.defineProperty(window, 'devicePixelRatio', { value, configurable: true });
}

describe('ChatComposer 拖入文件（浏览器形态）', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('拖入单个文件 ⇒ 在光标处插入文件名，并阻止默认行为', async () => {
    const wrapper = mountComposer('看他');
    const textarea = wrapper.find('textarea');
    (textarea.element as HTMLTextAreaElement).setSelectionRange(2, 2);

    const event = makeDragEvent('drop', [{ name: 'report.pdf' }]);
    textarea.element.dispatchEvent(event);
    await flushPromises();

    expect(event.defaultPrevented).toBe(true);
    expect(lastDraft(wrapper)).toBe('看他\nreport.pdf');
  });

  it('拖入多个文件 ⇒ 逐行插入', async () => {
    const wrapper = mountComposer();
    const textarea = wrapper.find('textarea');

    textarea.element.dispatchEvent(makeDragEvent('drop', [{ name: 'a.txt' }, { name: 'b.png' }]));
    await flushPromises();

    expect(lastDraft(wrapper)).toBe('a.txt\nb.png');
  });

  it('dragover 携带文件时阻止默认行为（否则 drop 不会触发）', () => {
    const wrapper = mountComposer();
    const textarea = wrapper.find('textarea');

    const withFiles = makeDragEvent('dragover', [{ name: 'a.txt' }]);
    textarea.element.dispatchEvent(withFiles);
    expect(withFiles.defaultPrevented).toBe(true);

    const withoutFiles = makeDragEvent('dragover');
    textarea.element.dispatchEvent(withoutFiles);
    expect(withoutFiles.defaultPrevented).toBe(false);
  });

  it('拖入的不是文件（如选中文本）⇒ 不插入、不改写草稿', async () => {
    const wrapper = mountComposer();
    const textarea = wrapper.find('textarea');
    const event = makeDragEvent('drop');

    textarea.element.dispatchEvent(event);
    await flushPromises();

    expect(event.defaultPrevented).toBe(false);
    expect(wrapper.emitted('updateDraft')).toBeUndefined();
  });
});

describe('ChatComposer 拖入文件（Tauri 形态）', () => {
  beforeEach(() => {
    dragDropHandlers.length = 0;
    enterTauriRuntime();
  });

  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    vi.clearAllMocks();
  });

  it('注册 onDragDropEvent，drop 事件里插入绝对路径', async () => {
    const wrapper = mountComposer('PLEASE');
    await flushPromises();
    stubEditorRect(wrapper);
    expect(dragDropHandlers).toHaveLength(1);

    dragDropHandlers[0](dropEvent(['/Users/alexa/Desktop/a b.txt'], INSIDE));
    await flushPromises();

    expect(lastDraft(wrapper)).toBe('PLEASE\n/Users/alexa/Desktop/a b.txt');
  });

  it('多文件 drop ⇒ 每个绝对路径一行', async () => {
    const wrapper = mountComposer();
    await flushPromises();
    stubEditorRect(wrapper);

    dragDropHandlers[0](dropEvent(['/tmp/a.txt', '/tmp/b.png'], INSIDE));
    await flushPromises();

    expect(lastDraft(wrapper)).toBe('/tmp/a.txt\n/tmp/b.png');
  });

  it('enter / over / leave 不插入任何文本', async () => {
    const wrapper = mountComposer();
    await flushPromises();

    dragDropHandlers[0]({ payload: { type: 'enter', paths: ['/tmp/a.txt'] } });
    dragDropHandlers[0]({ payload: { type: 'over' } });
    dragDropHandlers[0]({ payload: { type: 'leave' } });
    await flushPromises();

    expect(wrapper.emitted('updateDraft')).toBeUndefined();
  });

  it('DOM drop 在 Tauri 形态下只兜底 preventDefault，不插入（防双插）', async () => {
    const wrapper = mountComposer();
    await flushPromises();

    const event = makeDragEvent('drop', [{ name: 'a.txt' }]);
    wrapper.find('textarea').element.dispatchEvent(event);
    await flushPromises();

    expect(event.defaultPrevented).toBe(true);
    expect(wrapper.emitted('updateDraft')).toBeUndefined();
  });

  it('双插保护：原生已把路径插进输入框时，事件通道不再重复插入（只出现一次）', async () => {
    const wrapper = mountComposer();
    await flushPromises();

    stubEditorRect(wrapper);
    const textarea = wrapper.find('textarea').element as HTMLTextAreaElement;
    // 模拟「webview 原生插入」：值与光标都已经是这条路径之后。
    textarea.value = '/tmp/a.txt';
    textarea.setSelectionRange(12, 12);

    dragDropHandlers[0](dropEvent(['/tmp/a.txt'], INSIDE));
    await flushPromises();

    expect(wrapper.emitted('updateDraft')).toBeUndefined();
    expect(textarea.value).toBe('/tmp/a.txt');
  });

  it('双插保护不误杀：光标不在该路径之后时仍然插入', async () => {
    const wrapper = mountComposer();
    await flushPromises();

    stubEditorRect(wrapper);
    const textarea = wrapper.find('textarea').element as HTMLTextAreaElement;
    textarea.value = '/tmp/a.txt';
    textarea.setSelectionRange(0, 0);

    dragDropHandlers[0](dropEvent(['/tmp/a.txt'], INSIDE));
    await flushPromises();

    expect(lastDraft(wrapper)).toBe('/tmp/a.txt\n/tmp/a.txt');
  });

  it('落点在输入框外（侧边栏区域）⇒ 什么都不做，不写进聊天草稿', async () => {
    const wrapper = mountComposer();
    await flushPromises();
    stubEditorRect(wrapper);

    dragDropHandlers[0](dropEvent(['/tmp/a.txt'], OUTSIDE));
    await flushPromises();

    expect(wrapper.emitted('updateDraft')).toBeUndefined();
  });

  it('拿不到 position ⇒ fail-closed，不插入', async () => {
    const wrapper = mountComposer();
    await flushPromises();
    stubEditorRect(wrapper);

    dragDropHandlers[0](dropEvent(['/tmp/a.txt']));
    await flushPromises();

    expect(wrapper.emitted('updateDraft')).toBeUndefined();
  });

  it('DPR = 2 时按物理像素换算：命中插入、按 CSS 直比会误判的点不插入', async () => {
    setDevicePixelRatio(2);
    try {
      const wrapper = mountComposer();
      await flushPromises();
      stubEditorRect(wrapper);

      // css (100,100) 在 rect 内 ⇒ 物理 (200,200) 命中
      dragDropHandlers[0](dropEvent(['/tmp/hit.txt'], { x: 200, y: 200 }));
      await flushPromises();
      expect(lastDraft(wrapper)).toBe('/tmp/hit.txt');

      // 物理 (900,600) ⇒ css (450,300)，仍在 rect(10,20,300,120) 之外 ⇒ 不插入
      dragDropHandlers[0](dropEvent(['/tmp/miss.txt'], { x: 900, y: 600 }));
      await flushPromises();
      expect(lastDraft(wrapper)).toBe('/tmp/hit.txt');
    } finally {
      setDevicePixelRatio(1);
    }
  });

  it('输入框未挂载/尺寸为 0（rect 全 0）⇒ fail-closed', async () => {
    const wrapper = mountComposer();
    await flushPromises();
    // 不 stub：happy-dom 的 getBoundingClientRect 返回全 0，等价于「量不出尺寸」
    dragDropHandlers[0](dropEvent(['/tmp/a.txt'], { x: 0, y: 0 }));
    await flushPromises();

    expect(wrapper.emitted('updateDraft')).toBeUndefined();
  });

  it('组件卸载时调用 unlisten', async () => {
    const wrapper = mountComposer();
    await flushPromises();

    wrapper.unmount();

    expect(unlistenMock).toHaveBeenCalledTimes(1);
  });
});
