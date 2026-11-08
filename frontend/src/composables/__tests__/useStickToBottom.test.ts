import { describe, expect, it } from 'vitest';
import { useStickToBottom, HARD_BOTTOM_PX } from '../useStickToBottom';

/**
 * 贴底跟随的四条不变量（也顺便当作回归网）：
 * 1. FOLLOWING + 内容变高 → 贴到底；
 * 2. IDLE + 内容变高 → 不动 scrollTop，只置「有新内容」；
 * 3. 无用户的纯内容变化（含浏览器 scroll anchoring 补偿）**不改变状态**；
 * 4. 意图向上 + 离开硬底 → IDLE；回到硬底 → FOLLOWING。
 *
 * jsdom 没有布局，这里用一个把 `scrollHeight / clientHeight / scrollTop` 定义成可写 getter
 * 的假容器来代替真实滚动：`scrollTop` 赋值会像浏览器一样钳到 [0, scrollHeight - clientHeight]，
 * 并派发 scroll 事件。
 */

const VIEWPORT_HEIGHT = 100;
const FLUSH_MS = 40;

interface FakeScroller {
  el: HTMLElement;
  content: HTMLElement;
  setContentHeight: (value: number) => void;
}

function makeScroller(options: { fireScrollOnProgrammatic?: boolean } = {}): FakeScroller {
  const fireScroll = options.fireScrollOnProgrammatic ?? true;
  const el = document.createElement('div');
  document.body.appendChild(el);
  const content = document.createElement('div');
  el.appendChild(content);

  let contentHeight = 300;
  let scrollTop = 0;

  Object.defineProperty(el, 'clientHeight', { get: () => VIEWPORT_HEIGHT, configurable: true });
  Object.defineProperty(el, 'scrollHeight', { get: () => contentHeight, configurable: true });
  Object.defineProperty(el, 'scrollTop', {
    get: () => scrollTop,
    set: (value: number) => {
      const max = Math.max(0, contentHeight - VIEWPORT_HEIGHT);
      const next = Math.min(Math.max(0, value), max);
      if (next === scrollTop) {
        return;
      }
      scrollTop = next;
      if (fireScroll) {
        el.dispatchEvent(new Event('scroll'));
      }
    },
    configurable: true,
  });

  return {
    el,
    content,
    setContentHeight: (value: number) => {
      contentHeight = value;
    },
  };
}

function flush(): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, FLUSH_MS);
  });
}

function wheel(el: HTMLElement, deltaY: number): void {
  el.dispatchEvent(new WheelEvent('wheel', { deltaY, bubbles: true }));
}

describe('useStickToBottom', () => {
  it('跟随中新内容变高 → 自动贴到底', async () => {
    const scroller = makeScroller();
    const stick = useStickToBottom();
    stick.bind(scroller.el, scroller.content);
    expect(stick.following.value).toBe(true);

    scroller.setContentHeight(900);
    stick.notifyContentChanged();
    await flush();

    expect(scroller.el.scrollTop).toBe(900 - VIEWPORT_HEIGHT);
    expect(stick.hasNewContent.value).toBe(false);
  });

  it('向上滚离开底部 → 脱离；之后内容变高只提示，不动 scrollTop', async () => {
    const scroller = makeScroller();
    const stick = useStickToBottom();
    scroller.setContentHeight(900);
    stick.bind(scroller.el, scroller.content);
    await flush();

    // 用户滚轮向上：事件早于滚动生效，所以先派发事件、再让位置变化
    wheel(scroller.el, -120);
    scroller.el.scrollTop = 500;
    await flush();

    expect(stick.following.value).toBe(false);

    const before = scroller.el.scrollTop;
    scroller.setContentHeight(1400);
    stick.notifyContentChanged();
    await flush();

    expect(scroller.el.scrollTop).toBe(before);
    expect(stick.hasNewContent.value).toBe(true);
  });

  it('在底部继续向下滚（位置没动）→ 不脱钩', async () => {
    const scroller = makeScroller();
    const stick = useStickToBottom();
    scroller.setContentHeight(900);
    stick.bind(scroller.el, scroller.content);
    await flush();

    wheel(scroller.el, 120);
    await flush();

    expect(stick.following.value).toBe(true);
    expect(scroller.el.scrollTop).toBe(900 - VIEWPORT_HEIGHT);
  });

  it('内容变高引起的 scrollTop 增加（scroll anchoring 补偿）不改变状态', async () => {
    const scroller = makeScroller();
    const stick = useStickToBottom();
    scroller.setContentHeight(900);
    stick.bind(scroller.el, scroller.content);
    await flush();
    expect(stick.following.value).toBe(true);

    // 模拟浏览器锚定：内容在上方长高，浏览器把 scrollTop 一起加上去（无用户输入）
    scroller.setContentHeight(1300);
    scroller.el.scrollTop = 800 + 400;
    await flush();

    expect(stick.following.value).toBe(true);
    // 最终仍贴在最新内容上
    expect(scroller.el.scrollTop).toBe(1300 - VIEWPORT_HEIGHT);
  });

  it('无输入事件的位置变小（内容折叠或重排回缩）→ 不误脱离跟随', async () => {
    const scroller = makeScroller();
    const stick = useStickToBottom();
    scroller.setContentHeight(900);
    stick.bind(scroller.el, scroller.content);
    await flush();

    scroller.el.scrollTop = 400; // 仅位置变小，没有用户输入事件（如折叠/排版抖动）
    await flush();

    expect(stick.following.value).toBe(true);
  });

  it('向下滚回底部 → 恢复跟随并清掉「有新内容」', async () => {
    const scroller = makeScroller();
    const stick = useStickToBottom();
    scroller.setContentHeight(900);
    stick.bind(scroller.el, scroller.content);
    await flush();

    wheel(scroller.el, -120);
    scroller.el.scrollTop = 300;
    await flush();
    expect(stick.following.value).toBe(false);

    wheel(scroller.el, 120);
    scroller.el.scrollTop = 900 - VIEWPORT_HEIGHT;
    await flush();

    expect(stick.following.value).toBe(true);
    expect(stick.hasNewContent.value).toBe(false);
  });

  it('点「↓ 新活动」= scrollToLatest()：贴底并恢复跟随', async () => {
    const scroller = makeScroller();
    const stick = useStickToBottom();
    scroller.setContentHeight(900);
    stick.bind(scroller.el, scroller.content);
    await flush();

    wheel(scroller.el, -120);
    scroller.el.scrollTop = 200;
    await flush();
    scroller.setContentHeight(1500);
    stick.notifyContentChanged();
    await flush();
    expect(stick.hasNewContent.value).toBe(true);

    stick.scrollToLatest();
    await flush();

    expect(stick.following.value).toBe(true);
    expect(stick.hasNewContent.value).toBe(false);
    expect(scroller.el.scrollTop).toBe(1500 - VIEWPORT_HEIGHT);
  });

  it('硬底阈值是个小常数（避免退回 120px 那种「半个底部」模糊态）', () => {
    expect(HARD_BOTTOM_PX).toBeLessThanOrEqual(8);
  });

  it('onFollowChange 订阅方拿到每次状态变化', async () => {
    const seen: boolean[] = [];
    const scroller = makeScroller();
    const stick = useStickToBottom({ onFollowChange: (value) => seen.push(value) });
    scroller.setContentHeight(900);
    stick.bind(scroller.el, scroller.content);
    await flush();

    wheel(scroller.el, -120);
    scroller.el.scrollTop = 200;
    await flush();
    stick.scrollToLatest();
    await flush();

    expect(seen[0]).toBe(true);
    expect(seen).toContain(false);
    expect(seen[seen.length - 1]).toBe(true);
  });
});
