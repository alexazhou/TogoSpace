import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import AgentActivityItem from '../AgentActivityItem.vue';
import i18n from '../../../i18n';
import type { AgentActivity } from '../../../types';

/**
 * 执行环境徽标（§8）。
 *
 * 契约要点：后端 `AgentActivityMeta.to_dict()` 会剔掉值为 None 的字段，因此
 * **宿主态下 `current_container_id` / `container_name` 的键可能根本不存在**，
 * 不是 `null`。这几条用例就是按「键缺失」构造的。
 */
function activity(overrides: Partial<AgentActivity> = {}): AgentActivity {
  return {
    id: 1,
    agent_id: 8,
    team_id: 2,
    activity_type: 'tool_call',
    status: 'succeeded',
    title: '',
    detail: '',
    started_at: '2026-10-27 10:00:00.000000',
    duration_ms: 1200,
    metadata: {},
    ...overrides,
  };
}

function mountItem(item: AgentActivity) {
  return mount(AgentActivityItem, {
    props: { activity: item },
    global: { plugins: [i18n] },
  });
}

function envBadge(wrapper: ReturnType<typeof mountItem>) {
  return wrapper.find('.agent-activity-item__chip--env');
}

describe('AgentActivityItem 执行环境徽标', () => {
  it('容器态有 container_name 时优先展示容器名', () => {
    const wrapper = mountItem(activity({
      metadata: {
        tool_name: 'execute_bash',
        exec_env: 'container',
        current_container_id: 'c0ffee123456',
        container_name: 'agent-xiao-mei-1',
      },
    }));

    const badge = envBadge(wrapper);
    expect(badge.exists()).toBe(true);
    expect(badge.text()).toBe('agent-xiao-mei-1');
    expect(badge.attributes('data-exec-env')).toBe('container');
    // hover 提示里要有完整容器 ID：徽标正文是截断的短 ID 时才需要
    expect(badge.attributes('title')).toContain('c0ffee123456');
  });

  it('容器态缺 container_name 时回退到容器 ID 短 ID', () => {
    const wrapper = mountItem(activity({
      metadata: {
        tool_name: 'execute_bash',
        exec_env: 'container',
        current_container_id: 'c0ffee123456',
      },
    }));

    const badge = envBadge(wrapper);
    expect(badge.exists()).toBe(true);
    expect(badge.text()).toBe('c0ffee123456');
    expect(badge.attributes('data-exec-env')).toBe('container');
  });

  it('容器态但名字与 ID 都缺失时只给环境名，不渲染空胶囊', () => {
    const wrapper = mountItem(activity({
      metadata: {
        tool_name: 'execute_bash',
        exec_env: 'container',
      },
    }));

    const badge = envBadge(wrapper);
    expect(badge.exists()).toBe(true);
    expect(badge.text()).toBe('容器');
    expect(badge.attributes('data-exec-env')).toBe('container');
  });

  it('宿主态（只有 exec_env，两个容器键缺失）展示宿主徽标', () => {
    // 键直接不存在，不是 null：`to_dict()` 剔除 None 后的真实形状
    const metadata: Record<string, unknown> = { tool_name: 'execute_bash', exec_env: 'host' };
    expect('current_container_id' in metadata).toBe(false);
    expect('container_name' in metadata).toBe(false);

    const wrapper = mountItem(activity({ metadata }));

    const badge = envBadge(wrapper);
    expect(badge.exists()).toBe(true);
    expect(badge.text()).toBe('宿主');
    expect(badge.attributes('data-exec-env')).toBe('host');
  });

  it('三键全缺失（老记录 / Native driver 不报环境）不显示任何环境徽标', () => {
    const wrapper = mountItem(activity({
      metadata: { tool_name: 'execute_bash' },
    }));

    expect(envBadge(wrapper).exists()).toBe(false);
    // 顺带确认没有退化成「未知」之类的占位文案
    expect(wrapper.text()).not.toContain('未知');
  });

  it('metadata 里 exec_env 是非法值时也不显示徽标', () => {
    const wrapper = mountItem(activity({
      metadata: { tool_name: 'execute_bash', exec_env: 'sandbox' },
    }));

    expect(envBadge(wrapper).exists()).toBe(false);
  });
});
