import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import ActivityImageResult from '../activity/ActivityImageResult.vue';
import type { ActivityImage } from '../activity/activityViewModel';
import i18n from '../../../i18n';

/**
 * #361：read_image 图片块的渲染契约。
 *
 * 四条判据（对应任务单交付标准，缺一条就等于这次需求没做完）：
 * 1. 懒加载：进可视区之前**不许**把 `<img>` 挂进 DOM；进了才挂。
 * 2. 加载中 / 失败两态都有可见文案，失败不抛错也不留白。
 * 3. 点开大图有出口（遮罩 + Esc/点击遮罩关闭），加载失败时不给假出口。
 * 4. 尺寸有上限：`max-height` / `max-width` 必须写在样式里（happy-dom 不算布局，
 *    所以这条改成读组件源码断言，见最后一条用例的说明）。
 *
 * `IntersectionObserver` 在 happy-dom 里不存在，测试用可控替身：
 * `MockIntersectionObserver.instances` 收集实例，`trigger(true/false)` 模拟进出可视区。
 */
class MockIntersectionObserver implements IntersectionObserver {
  static instances: MockIntersectionObserver[] = [];

  readonly root: Element | Document | null = null;
  readonly rootMargin: string = '';

  readonly thresholds: ReadonlyArray<number> = [];

  disconnected = false;

  private readonly callback: IntersectionObserverCallback;

  constructor(callback: IntersectionObserverCallback) {
    this.callback = callback;
    MockIntersectionObserver.instances.push(this);
  }

  observe(): void {}

  unobserve(): void {}

  disconnect(): void {
    this.disconnected = true;
  }

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }

  trigger(isIntersecting: boolean): void {
    this.callback(
      [{ isIntersecting } as IntersectionObserverEntry],
      this,
    );
  }
}

const IMAGE: ActivityImage = {
  src: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==',
  mimeType: 'image/png',
  format: 'png',
  width: 171,
  height: 1000,
  sizeBytes: 25474,
  filePath: '/tmp/shots360/old_edge.png',
};

function mountImage(image: ActivityImage = IMAGE) {
  return mount(ActivityImageResult, {
    props: { image },
    global: { plugins: [i18n] },
  });
}

function lastObserver(): MockIntersectionObserver {
  const observer = MockIntersectionObserver.instances[MockIntersectionObserver.instances.length - 1];
  if (observer === undefined) {
    throw new Error('没有创建 IntersectionObserver');
  }
  return observer;
}

beforeEach(() => {
  MockIntersectionObserver.instances = [];
  vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('#361 图片块懒加载', () => {
  it('未进可视区：不挂 <img>，显示占位文案', () => {
    const wrapper = mountImage();
    expect(wrapper.find('[data-testid="activity-image-img"]').exists()).toBe(false);
    expect(wrapper.find('.agent-activity-image__frame').attributes('data-state')).toBe('idle');
    expect(wrapper.text()).toContain('图片（滚动到可见时加载）');
  });

  it('进可视区：挂出 <img>、src 是 data URL、状态变 loading 并停止观察', async () => {
    const wrapper = mountImage();
    lastObserver().trigger(true);
    await nextTick();

    const img = wrapper.find('[data-testid="activity-image-img"]');
    expect(img.exists()).toBe(true);
    expect(img.attributes('src')).toBe(IMAGE.src);
    expect(wrapper.find('.agent-activity-image__frame').attributes('data-state')).toBe('loading');
    expect(wrapper.text()).toContain('图片加载中…');
    expect(lastObserver().disconnected).toBe(true);
  });

  it('只是擦边未命中（isIntersecting=false）不触发加载', async () => {
    const wrapper = mountImage();
    lastObserver().trigger(false);
    await nextTick();
    expect(wrapper.find('[data-testid="activity-image-img"]').exists()).toBe(false);
  });

  it('加载完成：状态 loaded，占位文案消失', async () => {
    const wrapper = mountImage();
    lastObserver().trigger(true);
    await nextTick();
    await wrapper.find('[data-testid="activity-image-img"]').trigger('load');

    expect(wrapper.find('.agent-activity-image__frame').attributes('data-state')).toBe('loaded');
    expect(wrapper.text()).not.toContain('图片加载中…');
    expect(wrapper.text()).not.toContain('图片加载失败');
  });

  it('加载失败：显示失败文案、图片节点移除、不抛错', async () => {
    const wrapper = mountImage();
    lastObserver().trigger(true);
    await nextTick();
    await wrapper.find('[data-testid="activity-image-img"]').trigger('error');

    expect(wrapper.find('.agent-activity-image__frame').attributes('data-state')).toBe('error');
    expect(wrapper.text()).toContain('图片加载失败（数据缺失或已失效）');
    expect(wrapper.find('[data-testid="activity-image-img"]').exists()).toBe(false);
  });

  it('没有 IntersectionObserver 的环境：退化成立即加载，而不是永远不加载', async () => {
    vi.unstubAllGlobals();
    vi.stubGlobal('IntersectionObserver', undefined);

    const wrapper = mountImage();
    await nextTick();
    expect(wrapper.find('[data-testid="activity-image-img"]').exists()).toBe(true);
  });

  it('同一实例换成另一张图：重新进入 loading 态', async () => {
    const wrapper = mountImage();
    lastObserver().trigger(true);
    await nextTick();
    await wrapper.find('[data-testid="activity-image-img"]').trigger('load');
    expect(wrapper.find('.agent-activity-image__frame').attributes('data-state')).toBe('loaded');

    await wrapper.setProps({ image: { ...IMAGE, src: 'data:image/png;base64,AAAA' } });
    expect(wrapper.find('.agent-activity-image__frame').attributes('data-state')).toBe('loading');
  });
});

describe('#361 点开大图', () => {
  it('加载成功后可点开遮罩大图，Esc 关闭', async () => {
    const wrapper = mountImage({ ...IMAGE, }, );
    lastObserver().trigger(true);
    await nextTick();
    await wrapper.find('[data-testid="activity-image-img"]').trigger('load');

    await wrapper.find('.agent-activity-image__frame').trigger('click');
    const lightbox = document.querySelector('[data-testid="activity-image-lightbox"]');
    expect(lightbox).not.toBeNull();
    expect(lightbox?.querySelector('img')?.getAttribute('src')).toBe(IMAGE.src);

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    await nextTick();
    expect(document.querySelector('[data-testid="activity-image-lightbox"]')).toBeNull();
  });

  it('点关闭按钮可关闭', async () => {
    const wrapper = mountImage();
    lastObserver().trigger(true);
    await nextTick();
    await wrapper.find('[data-testid="activity-image-img"]').trigger('load');
    await wrapper.find('.agent-activity-image__frame').trigger('click');

    const closeButton = document.querySelector('.agent-activity-image__lightbox-close') as HTMLElement;
    expect(closeButton).not.toBeNull();
    closeButton.click();
    await nextTick();
    expect(document.querySelector('[data-testid="activity-image-lightbox"]')).toBeNull();
  });

  it('未加载成功（还在占位/失败）点击不给假出口', async () => {
    const wrapper = mountImage();
    await wrapper.find('.agent-activity-image__frame').trigger('click');
    expect(document.querySelector('[data-testid="activity-image-lightbox"]')).toBeNull();
  });

  it('组件卸载后不再响应 Esc（监听器已摘）', async () => {
    const wrapper = mountImage();
    lastObserver().trigger(true);
    await nextTick();
    await wrapper.find('[data-testid="activity-image-img"]').trigger('load');
    await wrapper.find('.agent-activity-image__frame').trigger('click');
    wrapper.unmount();

    // 卸载前遮罩还在 body 里（Teleport 目标），Esc 不该报错、也不该留下监听器
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(document.querySelector('[data-testid="activity-image-lightbox"]')).toBeNull();
  });
});

describe('#361 说明行与尺寸上限', () => {
  it('说明行展示尺寸/格式/大小/文件名', () => {
    const wrapper = mountImage();
    expect(wrapper.find('.agent-activity-image__meta').text()).toBe('171×1000 · png · 25 KB · old_edge.png');
  });

  it('样式里必须有 max-height 与 max-width 上限（防止撑破卡片）', () => {
    // 为什么不写「渲染后量高度」：happy-dom 不做布局，量出来恒 0，断言只会假绿。
    // 这条改读组件源码文本，直接钉住「容器和图片都受上限约束」这个契约；
    // 删掉任一上限即变红（变异方向见交付说明）。
    const source = readFileSync('src/components/agent/activity/ActivityImageResult.vue', 'utf8');
    const frameRule = source.split('.agent-activity-image__frame {')[1]?.split('}')[0] ?? '';
    const imgRule = source.split('.agent-activity-image__img {')[1]?.split('}')[0] ?? '';
    expect(frameRule).toContain('max-width: 100%');
    expect(frameRule).toContain('max-height: 200px');
    expect(imgRule).toContain('max-width: 100%');
    expect(imgRule).toContain('max-height: 200px');
    expect(imgRule).toContain('object-fit: contain');
  });
});
