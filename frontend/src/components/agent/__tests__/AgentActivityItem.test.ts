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

describe('AgentActivityItem 容器工具展示', () => {
  it('创建容器展示本地化名称与镜像参数', () => {
    const wrapper = mountItem(activity({
      metadata: {
        tool_name: 'create_container',
        tool_arguments: { image: 'ubuntu:24.04' },
      },
    }));

    expect(wrapper.find('.agent-activity-item__chip--mono').text()).toBe('创建容器');
    expect(wrapper.find('.agent-activity-item__tool-args').text()).toBe('ubuntu:24.04');
  });

  it('进入容器展示本地化名称与容器 ID', () => {
    const wrapper = mountItem(activity({
      metadata: {
        tool_name: 'enter_container',
        tool_arguments: { container_id: 'c0ffee123456' },
      },
    }));

    expect(wrapper.find('.agent-activity-item__chip--mono').text()).toBe('进入容器');
    expect(wrapper.find('.agent-activity-item__tool-args').text()).toBe('c0ffee123456');
  });

  it('查看容器与退出容器展示本地化名称且不渲染空参数', () => {
    for (const [toolName, label] of [
      ['list_containers', '查看容器'],
      ['exit_to_host', '退出容器'],
    ] as const) {
      const wrapper = mountItem(activity({
        metadata: { tool_name: toolName, tool_arguments: {} },
      }));

      expect(wrapper.find('.agent-activity-item__chip--mono').text()).toBe(label);
      expect(wrapper.find('.agent-activity-item__tool-args').exists()).toBe(false);
    }
  });
});

describe('AgentActivityItem 时间与房间工具展示', () => {
  it('获取时间展示本地化名称与时区参数', () => {
    const wrapper = mountItem(activity({
      metadata: {
        tool_name: 'get_time',
        tool_arguments: { timezone: 'Asia/Shanghai' },
      },
    }));

    expect(wrapper.find('.agent-activity-item__chip--mono').text()).toBe('获取时间');
    expect(wrapper.find('.agent-activity-item__tool-args').text()).toBe('Asia/Shanghai');
  });

  it('查询房间展示本地化名称与房间名参数', () => {
    const wrapper = mountItem(activity({
      metadata: {
        tool_name: 'get_room_info',
        tool_arguments: { room_name: '研发组' },
      },
    }));

    expect(wrapper.find('.agent-activity-item__chip--mono').text()).toBe('查询房间');
    expect(wrapper.find('.agent-activity-item__tool-args').text()).toBe('研发组');
  });

  it('无参数调用时不渲染空参数', () => {
    for (const [toolName, label] of [
      ['get_time', '获取时间'],
      ['get_room_info', '查询房间'],
    ] as const) {
      const wrapper = mountItem(activity({
        metadata: { tool_name: toolName, tool_arguments: {} },
      }));

      expect(wrapper.find('.agent-activity-item__chip--mono').text()).toBe(label);
      expect(wrapper.find('.agent-activity-item__tool-args').exists()).toBe(false);
    }
  });
});

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

  it('宿主态（只有 exec_env，两个容器键缺失）不显示宿主徽标', () => {
    // 键直接不存在，不是 null：`to_dict()` 剔除 None 后的真实形状
    const metadata: Record<string, unknown> = { tool_name: 'execute_bash', exec_env: 'host' };
    expect('current_container_id' in metadata).toBe(false);
    expect('container_name' in metadata).toBe(false);

    const wrapper = mountItem(activity({ metadata }));

    expect(envBadge(wrapper).exists()).toBe(false);
    expect(wrapper.text()).not.toContain('宿主');
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

/**
 * bash 行首行「执行命令 + 描述」的中线对齐。
 *
 * `执行命令` 是固定 18px 高的 chip，右侧描述早先被 `--expanded` / `--tool-result`
 * 的 font-size/line-height 带着走，行盒比 chip 高，视觉上比左侧低 1.5~2.5px。
 * 修法是把描述钉成 18px 行盒并垂直居中（见组件里 `--bash-description` 规则）。
 * jsdom 不做布局，所以这里只锁两件事：描述必须带上这个类；被压成单行后仍有 title 看全文。
 */
describe('AgentActivityItem bash 首行描述', () => {
  const bashArguments = { command: 'cat src/version.py', description: '查版本号位置' };

  it('执行中（尚未有结果）：描述带 --bash-description 类，和 chip 同行', () => {
    const wrapper = mountItem(activity({
      status: 'started',
      metadata: { tool_name: 'execute_bash', tool_arguments: bashArguments },
    }));

    const summary = wrapper.find('.agent-activity-item__summary--bash-description');
    expect(summary.exists()).toBe(true);
    expect(summary.text()).toBe('查版本号位置');
    expect(summary.classes()).toContain('agent-activity-item__summary--code');
    expect(wrapper.find('.agent-activity-item__row').element.contains(summary.element)).toBe(true);
  });

  it('已完成（展开态）：描述仍是单行类，并保留 title 供查看全文', () => {
    const wrapper = mountItem(activity({
      metadata: {
        tool_name: 'execute_bash',
        tool_arguments: bashArguments,
        tool_result: 'version = "0.4.2-beta2"',
      },
    }));

    expect(wrapper.classes()).toContain('agent-activity-item--bash-result');
    const summary = wrapper.find('.agent-activity-item__summary--bash-description');
    expect(summary.exists()).toBe(true);
    // 展开态以前把 title 置空；描述压成单行后必须保留，否则截断就看不到全文
    expect(summary.attributes('title')).toBe('查版本号位置');
  });

  it('没有 description 时不套 --bash-description（不给其它工具的描述改行盒）', () => {
    const wrapper = mountItem(activity({
      metadata: { tool_name: 'execute_bash', tool_arguments: { command: 'ls' }, tool_result: 'a\nb' },
    }));

    expect(wrapper.find('.agent-activity-item__summary--bash-description').exists()).toBe(false);
  });
});

/**
 * Token 展示：推理与上下文压缩共用同一个尾部位置。
 * - 推理：流式中显示「估算 prompt + 已输出」，结束后显示后端 usage 总量。
 * - 压缩：进行中显示「↑ 压缩前 ↓ 摘要输出估算」，结束后显示「压缩前 → 压缩后」。
 */
describe('AgentActivityItem token 展示', () => {
  function tokenEl(wrapper: ReturnType<typeof mountItem>) {
    return wrapper.find('.agent-activity-item__tokens');
  }

  it('推理中显示 prompt 与已输出 token 的估算', () => {
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'started',
      metadata: { estimated_prompt_tokens: 1000, current_completion_tokens: 20 },
    }));

    expect(tokenEl(wrapper).text()).toBe('估算 1020');
  });

  it('推理结束显示 usage 总量', () => {
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'succeeded',
      metadata: { estimated_prompt_tokens: 1000, final_total_tokens: 1200 },
    }));

    expect(tokenEl(wrapper).text()).toBe('tokens 1200');
  });

  it('压缩中显示压缩前规模与摘要输出估算（升降箭头）', () => {
    const wrapper = mountItem(activity({
      activity_type: 'compact',
      status: 'started',
      metadata: { context_tokens_before: 80000, current_completion_tokens: 120 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 80000 ↓ 120');
  });

  it('压缩结束显示压缩前后对比', () => {
    const wrapper = mountItem(activity({
      activity_type: 'compact',
      status: 'succeeded',
      metadata: { context_tokens_before: 80000, context_tokens_after: 4200 },
    }));

    expect(tokenEl(wrapper).text()).toBe('tokens 80000 → 4200');
  });
});
