/**
 * #338：顶部菜单栏「退出登录」按钮的显隐 —— 桌面 app 不渲染，浏览器仍按 authEnabled 渲染。
 *
 * 判据分两侧（缺一侧就是「改完只测了自己想看到的那一半」）：
 * 1. app 模式（`window.__TAURI_INTERNALS__` 存在 ⇒ `isTauri()` 为真）：即使 `authEnabled` 为真也**不渲染**；
 * 2. web 模式（同上标记不存在）：`authEnabled` 为真时**仍然渲染**，为假时不渲染（既有行为不变）。
 *
 * 直接 mount 本组件而不是 mount `TopBar`：本用例要钉的是「这一处显隐」，
 * 经父组件会引入一堆无关 props 与 DOM，失败信息也难定位。
 */

import { afterEach, describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import TopBarUtilityGroup from '../TopBarUtilityGroup.vue';
import i18n from '../../../i18n';

type GlobalWithTauri = typeof globalThis & {
  window: Window & { __TAURI_INTERNALS__?: unknown };
};

function setAppMode(enabled: boolean): void {
  const w = (globalThis as unknown as GlobalWithTauri).window;
  if (enabled) {
    // Tauri 注入的是对象（真实运行时是一组 IPC 函数），这里只要有这个 key 即可 ——
    // `isTauri()` 的判据就是 `'__TAURI_INTERNALS__' in window`。
    (w as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = { invoke: () => undefined };
  } else {
    delete (w as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
  }
}

function mountGroup(props: { authEnabled?: boolean; hasUpdate?: boolean } = {}) {
  return mount(TopBarUtilityGroup, {
    props: { isLightMode: false, ...props },
    global: { plugins: [i18n] },
  });
}

afterEach(() => {
  setAppMode(false);
});

describe('TopBarUtilityGroup 退出登录按钮（#338）', () => {
  it('app 模式：即使 authEnabled 为真也不渲染退出按钮', () => {
    setAppMode(true);

    const wrapper = mountGroup({ authEnabled: true, hasUpdate: false });

    expect(wrapper.find('.logout-button').exists()).toBe(false);
    // 反向对照：同一份 fixture 下别的控件还在，证明「没找到按钮」不是整块没渲染。
    expect(wrapper.find('.topbar-utility-group').exists()).toBe(true);
    expect(wrapper.find('.language-switch').exists()).toBe(true);
  });

  it('web 模式：authEnabled 为真时仍然渲染退出按钮（行为不变）', () => {
    setAppMode(false);

    const wrapper = mountGroup({ authEnabled: true, hasUpdate: false });

    const button = wrapper.find('.logout-button');
    expect(button.exists()).toBe(true);
    expect(button.attributes('type')).toBe('button');
  });

  it('web 模式：authEnabled 为假时也不渲染（既有行为不变）', () => {
    setAppMode(false);

    const wrapper = mountGroup({ authEnabled: false });

    expect(wrapper.find('.logout-button').exists()).toBe(false);
  });

  it('app 模式下登出确认弹窗不会被误触打开', async () => {
    setAppMode(true);

    const wrapper = mountGroup({ authEnabled: true });
    await wrapper.vm.$nextTick();

    // 按钮不存在 ⇒ 没有任何入口能把 logoutConfirmOpen 置真，弹窗不应出现在 DOM 里。
    expect(wrapper.find('.logout-button').exists()).toBe(false);
    expect(wrapper.findAll('button').some(b => b.classes().includes('logout-button'))).toBe(false);
  });
});
