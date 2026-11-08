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
 *
 * 推理（`llm_infer`）自 #167 起**拆成两个值**：`↑ 输入 ↓ 输出`；
 * 自 #168 起**逐段**判定估算（可以「输入真值 + 输出估算」混色）；
 * 自 #169 起 metadata 键名对齐上游 `OpenAIUsage`（真值三键同名，估算加 `estimated_` 前缀）。
 * - 流中：`estimated_prompt_tokens` + `estimated_completion_tokens`（**估算**，浅色 class）
 * - 结束后：`prompt_tokens` + `completion_tokens`（真值，正常色）；
 *   `completion_tokens` 缺失时回落 `estimated_completion_tokens`
 *   （网关不回 usage 时 `agentTurnRunner` 的兜底路径只写这个键）
 * - `total_tokens` **不参与显示**（键仍在 metadata 里）
 *
 * 压缩（`compact`）**显示行为不在改动范围**：仍是「进行中 ↑ 压缩前 ↓ 摘要输出估算」与
 * 「结束后 压缩前 → 压缩后」。T6-a/T6-b 只随键名迁移改了 metadata 构造，断言文本一字未动。
 */
describe('AgentActivityItem token 展示', () => {
  function tokenEl(wrapper: ReturnType<typeof mountItem>) {
    return wrapper.find('.agent-activity-item__tokens');
  }

  /**
   * 估算标记所在的元素。
   *
   * #168 起估算 class 挂在**每个段**（`--tokens-segment--estimated`）而不是容器上，
   * 所以这里同时匹配两种写法：
   * - 容器级 `--tokens--estimated`（#167 的形态，现在不再产生）
   * - 段级 `--tokens-segment--estimated`（现在唯一的形态）
   * 用组合选择器而不是留旧选择器，是为了让 T2/T6 那几条「不该有估算色」的断言
   * 从「恒假」恢复成真正会咬人的判据（容器 class 已不存在，只查容器会让断言永远绿）。
   */
  function estimatedTokensEl(wrapper: ReturnType<typeof mountItem>) {
    return wrapper.find(
      '.agent-activity-item__tokens--estimated, .agent-activity-item__tokens-segment--estimated',
    );
  }

  /** 带估算 class 的**段**元素（按 DOM 顺序），用于逐段断言。 */
  function estimatedSegments(wrapper: ReturnType<typeof mountItem>) {
    return wrapper.findAll('.agent-activity-item__tokens-segment').filter(
      (segment) => segment.classes().includes('agent-activity-item__tokens-segment--estimated'),
    );
  }

  function segments(wrapper: ReturnType<typeof mountItem>) {
    return wrapper.findAll('.agent-activity-item__tokens-segment');
  }

  // ---------------- 推理（LLM_INFER）：↑ 输入 ↓ 输出 ----------------

  it('T1 推理流中：显示估算的 输入/输出 两个值，两段都带估算浅色 class', () => {
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'started',
      metadata: { estimated_prompt_tokens: 1000, estimated_completion_tokens: 20 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1000 ↓ 20');
    // 逐段着色：两段都是估算 ⇒ 两个段元素各带 class，容器本身不再带。
    expect(segments(wrapper)).toHaveLength(2);
    expect(estimatedSegments(wrapper)).toHaveLength(2);
    // 用 textContent 而不是 text()：后者会 trim，断不出「↓ 前那个前导空格」是否还在。
    expect(segments(wrapper).map((segment) => segment.element.textContent)).toEqual(['↑ 1000', ' ↓ 20']);
  });

  it('T2 推理结束：显示 usage 真值的 输入/输出，不带估算 class，且不再显示 total', () => {
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'succeeded',
      metadata: {
        estimated_prompt_tokens: 1000,
        prompt_tokens: 1200,
        completion_tokens: 200,
        total_tokens: 1400,
      },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1200 ↓ 200');
    expect(estimatedTokensEl(wrapper).exists()).toBe(false);
    // total_tokens 保留在 metadata 里但不再显示（1400 不得出现）。
    expect(tokenEl(wrapper).text()).not.toContain('1400');
  });

  it('T2-b 两个 completion 键共存（估算=0）：取真值，不显示 ↓ 0', () => {
    // 共存是**活形态**：#169 起流中 chunk 带 usage 写 `completion_tokens`，后续不带
    // usage 的 chunk 写 `estimated_completion_tokens`，浅合并下两键天然并存（M5）。
    // 历史行同理（老键 `current_completion_tokens` 与 `final_completion_tokens` 共存
    // 48536 条，其中 44112 条估算侧=0 而真值>0），只是老键名按 Operator 决定不做双读、
    // 前端读不到即不显示，故本行用新键名构造。
    // 写反优先级（`estimated ?? completion`）就是用户可见的 `↓ 0`。
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'succeeded',
      metadata: {
        estimated_prompt_tokens: 1000,
        prompt_tokens: 1200,
        completion_tokens: 44,
        estimated_completion_tokens: 0,
      },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1200 ↓ 44');
    expect(tokenEl(wrapper).text()).not.toContain('↓ 0');
    expect(estimatedTokensEl(wrapper).exists()).toBe(false);
  });

  it('T3 推理结束但缺 completion_tokens：输出量回落 estimated_completion_tokens', () => {
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'succeeded',
      metadata: { prompt_tokens: 1200, estimated_completion_tokens: 200 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1200 ↓ 200');
    // 逐段着色：输入量已是真值 ⇒ ↑ 段不是估算；输出量只有流中估算 ⇒ ↓ 段是估算。
    expect(segments(wrapper).map((segment) => segment.element.textContent)).toEqual(['↑ 1200', ' ↓ 200']);
    expect(segments(wrapper)[0].classes()).not.toContain('agent-activity-item__tokens-segment--estimated');
    expect(segments(wrapper)[1].classes()).toContain('agent-activity-item__tokens-segment--estimated');
  });

  it('T4 只有输入量：只显示 ↑，不出现 ↓ 0 这种假值', () => {
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'started',
      metadata: { estimated_prompt_tokens: 1000 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1000');
    expect(tokenEl(wrapper).text()).not.toContain('↓');
  });

  it('T5 推理只有总量没有输入输出：不渲染 tokens 元素', () => {
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'succeeded',
      metadata: { total_tokens: 1200 },
    }));

    // total_tokens 不再参与显示 ⇒ 没有可显示的输入/输出量 ⇒ 整个 tokens 元素不渲染。
    expect(tokenEl(wrapper).exists()).toBe(false);
  });

  it('T5-b 推理 metadata 完全为空：不渲染 tokens 元素', () => {
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'succeeded',
      metadata: {},
    }));

    expect(tokenEl(wrapper).exists()).toBe(false);
  });

  // ---------------- 逐段着色（#168 P9–P12） ----------------

  it('P9 半真半估：输入真值 + 输出估算 ⇒ ↑ 段无 class、↓ 段有 class', () => {
    // ⚠️ 该 metadata 形态在当前后端**不可达**（`apply_usage` 里两个 final 键同生同死，
    // DB 49015 条 LLM_INFER 中 fp_only / fc_only 均为 0）。这里守的是**组件契约**：
    // 将来后端把 usage 字段改成 Optional 后，这个输入必须立刻显示正确。
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'succeeded',
      metadata: { prompt_tokens: 1200, estimated_completion_tokens: 200 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1200 ↓ 200');
    const parts = segments(wrapper);
    expect(parts).toHaveLength(2);
    expect(parts[0].classes()).not.toContain('agent-activity-item__tokens-segment--estimated');
    expect(parts[1].classes()).toContain('agent-activity-item__tokens-segment--estimated');
  });

  it('P10 两段都估算：↑ ↓ 段都带 class', () => {
    // 真正可达的「无真值」路径：`usage is None`（网关不回 usage），两键都缺。
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'succeeded',
      metadata: { estimated_prompt_tokens: 1000, estimated_completion_tokens: 20 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1000 ↓ 20');
    expect(estimatedSegments(wrapper)).toHaveLength(2);
  });

  it('P11 两段都真值：↑ ↓ 段都不带 class', () => {
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'succeeded',
      metadata: { prompt_tokens: 1200, completion_tokens: 200 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1200 ↓ 200');
    expect(estimatedTokensEl(wrapper).exists()).toBe(false);
  });

  it('P12 只有一段渲染：只出 ↑ 段（带 class），不存在 ↓ 段元素', () => {
    const wrapper = mountItem(activity({
      activity_type: 'llm_infer',
      status: 'started',
      metadata: { estimated_prompt_tokens: 1000 },
    }));

    const parts = segments(wrapper);
    expect(parts).toHaveLength(1);
    expect(parts[0].text()).toBe('↑ 1000');
    expect(parts[0].classes()).toContain('agent-activity-item__tokens-segment--estimated');
    // 「不存在 ↓ 段元素」比「文本里没有 ↓」更强：连残留的 class 都没地方挂。
    expect(wrapper.find('.agent-activity-item__tokens-segment--estimated:nth-child(2)').exists()).toBe(false);
  });

  // ---------------- 压缩（COMPACT）：不在改动范围，断言原样 ----------------

  it('T6-a 压缩中显示压缩前规模与摘要输出估算（升降箭头）', () => {
    const wrapper = mountItem(activity({
      activity_type: 'compact',
      status: 'started',
      metadata: { context_tokens_before: 80000, estimated_completion_tokens: 120 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 80000 ↓ 120');
    // COMPACT 不是「估算/真值」两态语义，不该挂估算 class。
    expect(estimatedTokensEl(wrapper).exists()).toBe(false);
  });

  it('T6-b 压缩结束显示压缩前后对比', () => {
    const wrapper = mountItem(activity({
      activity_type: 'compact',
      status: 'succeeded',
      metadata: { context_tokens_before: 80000, context_tokens_after: 4200 },
    }));

    expect(tokenEl(wrapper).text()).toBe('tokens 80000 → 4200');
    expect(estimatedTokensEl(wrapper).exists()).toBe(false);
  });
});
