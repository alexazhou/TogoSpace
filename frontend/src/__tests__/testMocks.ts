import { vi } from 'vitest';

/**
 * 测试公用 mock。
 *
 * 这之前 `PendingMessageDock.test.ts` 与 `PendingMessageBar.test.ts` 各自维护了一份
 * 完全相同的 `FakeResizeObserver`（连注释都在讲同一件事），改一处漏一处是迟早的事。
 *
 * 为什么需要它：happy-dom 没有 `ResizeObserver` 实现，也没有真实布局 ——
 * 「预览行是否被截断」靠 `scrollWidth > clientWidth` 这类几何量判定，而 happy-dom 里
 * 恒为 0/0。因此测试必须能**手动触发**观察器回调，在触发前把几何值覆写成想要的形态。
 */
export class FakeResizeObserver {
  static instances: FakeResizeObserver[] = [];

  callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    FakeResizeObserver.instances.push(this);
  }

  observe(): void {}

  unobserve(): void {}

  disconnect(): void {}

  /** 手动触发一次回调（等价于浏览器报告了一次尺寸变化）。 */
  trigger(): void {
    this.callback([], this as unknown as ResizeObserver);
  }
}

/** 清空实例表并把 `ResizeObserver` 全局替换为假实现（放 `beforeEach` 里）。 */
export function installFakeResizeObserver(): void {
  FakeResizeObserver.instances = [];
  vi.stubGlobal('ResizeObserver', FakeResizeObserver);
}

/** 触发全部已创建的观察器。 */
export function triggerAllResizeObservers(): void {
  FakeResizeObserver.instances.forEach((observer) => observer.trigger());
}

/** 只触发最近创建的那个（组件内观察器按创建顺序入表）。 */
export function triggerLastResizeObserver(): void {
  FakeResizeObserver.instances[FakeResizeObserver.instances.length - 1]?.trigger();
}
