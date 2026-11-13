import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { createResizeObserver, useResizeObserver } from '../useResizeObserver';

/**
 * 记录行为的假观察器。
 *
 * 为什么不复用 `src/__tests__/testMocks.ts` 里的 `FakeResizeObserver`：
 * 那个是为**组件测试**服务的（只需要「能手动触发回调」），这里要断言的是
 * 「观察了哪些元素 / 被 disconnect 几次 / 重建过几次」这类**调用轨迹**，
 * 两边的意图不同，硬合并会让组件测试的 mock 变重。
 */
class RecordingResizeObserver {
  static instances: RecordingResizeObserver[] = [];

  observed: Element[] = [];

  disconnectCount = 0;

  callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    RecordingResizeObserver.instances.push(this);
  }

  observe(target: Element): void {
    this.observed.push(target);
  }

  unobserve(): void {}

  disconnect(): void {
    this.disconnectCount += 1;
  }

  trigger(entries: ResizeObserverEntry[] = []): void {
    this.callback(entries, this as unknown as ResizeObserver);
  }
}

function makeElement(label: string): HTMLElement {
  const el = document.createElement('div');
  el.dataset.label = label;
  document.body.appendChild(el);
  return el;
}

beforeEach(() => {
  RecordingResizeObserver.instances = [];
  vi.stubGlobal('ResizeObserver', RecordingResizeObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('createResizeObserver（纯工厂）', () => {
  it('目标为空时不构造观察器（惰性创建）', () => {
    const handle = createResizeObserver(() => {});

    handle.observe(null);
    handle.observe(undefined);

    expect(RecordingResizeObserver.instances).toHaveLength(0);
  });

  it('多个目标复用同一个观察器实例（viewport + content 场景）', () => {
    const a = makeElement('a');
    const b = makeElement('b');
    const handle = createResizeObserver(() => {});

    handle.observe(a);
    handle.observe(b);

    expect(RecordingResizeObserver.instances).toHaveLength(1);
    expect(RecordingResizeObserver.instances[0].observed).toEqual([a, b]);
  });

  it('disconnect 释放实例，再次 observe 会重建（bind/dispose 反复调用不留旧观察器）', () => {
    const a = makeElement('a');
    const c = makeElement('c');
    const handle = createResizeObserver(() => {});

    handle.observe(a);
    handle.disconnect();
    expect(RecordingResizeObserver.instances[0].disconnectCount).toBe(1);

    handle.observe(c);
    expect(RecordingResizeObserver.instances).toHaveLength(2);
    expect(RecordingResizeObserver.instances[1].observed).toEqual([c]);
  });

  it('回调只透传第一条 entry（调用方只关心「变了」，不需要遍历）', () => {
    const spy = vi.fn();
    const handle = createResizeObserver(spy);
    handle.observe(makeElement('x'));

    const first = { target: makeElement('first') } as unknown as ResizeObserverEntry;
    const second = { target: makeElement('second') } as unknown as ResizeObserverEntry;
    RecordingResizeObserver.instances[0].trigger([first, second]);

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith(first);
    handle.disconnect();
  });

  it('环境没有 ResizeObserver 时静默降级，不抛错', () => {
    vi.stubGlobal('ResizeObserver', undefined);
    const handle = createResizeObserver(() => {});

    expect(() => handle.observe(makeElement('a'))).not.toThrow();
    expect(() => handle.disconnect()).not.toThrow();
  });
});

describe('useResizeObserver（组件级）', () => {
  it('ref 绑定元素后自动开始观察，组件卸载时自动 disconnect', async () => {
    const spy = vi.fn();
    const Comp = defineComponent({
      setup() {
        const el = ref<HTMLElement | null>(null);
        useResizeObserver(el, spy);
        return () => h('div', { ref: el });
      },
    });

    const wrapper = mount(Comp);
    await nextTick();

    expect(RecordingResizeObserver.instances).toHaveLength(1);
    expect(RecordingResizeObserver.instances[0].observed).toHaveLength(1);

    wrapper.unmount();
    expect(RecordingResizeObserver.instances[0].disconnectCount).toBeGreaterThanOrEqual(1);
  });

  it('多个 ref 一起观察（graphRef + canvasRef 场景）', async () => {
    const spy = vi.fn();
    const Comp = defineComponent({
      setup() {
        const a = ref<HTMLElement | null>(null);
        const b = ref<HTMLElement | null>(null);
        useResizeObserver([a, b], spy);
        return () => h('div', [h('div', { ref: a }), h('div', { ref: b })]);
      },
    });

    const wrapper = mount(Comp);
    await nextTick();

    expect(RecordingResizeObserver.instances[0].observed).toHaveLength(2);
    wrapper.unmount();
  });

  it('目标节点被替换时重挂到新节点（v-if 换代场景）', async () => {
    const spy = vi.fn();
    const Comp = defineComponent({
      props: { showA: { type: Boolean, default: true } },
      setup(props) {
        const el = ref<HTMLElement | null>(null);
        useResizeObserver(el, spy);
        return () => (props.showA
          ? h('div', { ref: el, class: 'node-a' })
          : h('span', { ref: el, class: 'node-b' }));
      },
    });

    const wrapper = mount(Comp);
    await nextTick();

    await wrapper.setProps({ showA: false });
    await nextTick();

    // 旧节点必须先被释放（否则节点换代后旧节点的回调还在触发，且观察器会越积越多）
    const first = RecordingResizeObserver.instances[0];
    expect(first.disconnectCount).toBeGreaterThanOrEqual(1);

    const last = RecordingResizeObserver.instances[RecordingResizeObserver.instances.length - 1];
    const observedNode = last.observed[last.observed.length - 1] as HTMLElement;
    expect(observedNode.className).toBe('node-b');

    wrapper.unmount();
  });
});
