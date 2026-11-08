/**
 * #169：`AgentActivityMeta` token 键改名后，前端读数的判据（M7 / M8 / M10）。
 *
 * ## 与主测试文件的分工
 * `AgentActivityItem.test.ts` 里的 T1–T6、P9–P12 是**显示格式**判据（#167/#168 定的），
 * 本文件是**键名契约**判据（#169 定的）：新键读到什么、老键读不到会怎样、
 * `??` 与 `||` 的区别。分开写是为了将来改 schema 时能一眼看出哪些断言会跟着动。
 *
 * ## 三条边界（都来自 Operator / PM 的明确要求）
 * 1. **M8 老数据**：Operator 定「不迁移历史、读取不到不报错即可」。但
 *    `estimated_prompt_tokens` **名字没改**（一直是 `estimated_` 前缀），所以老行里它仍可读
 *    ⇒ 老行会显示 `↑ N` 而**没有 ↓ 段**。不是「完全空白」。不许为了「彻底空」去屏蔽它。
 * 2. **M10 `0` 不能被当空**：`prompt_tokens ?? estimated_prompt_tokens` 若写成 `||`，
 *    `prompt_tokens: 0`（真值确实是 0，比如模型什么都没读）会掉到估算分支，
 *    于是把估算值当 usage 真值显示成正常色。后端当前不可达该形态（`OpenAIUsage` 三字段
 *    默认 0 而非 None），但组件契约要守。
 * 3. **失败条目不区分估算/真值**（Operator 2026-11-09 00:45 定），不作判据。
 */
import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import AgentActivityItem from '../AgentActivityItem.vue';
import i18n from '../../../i18n';
import type { AgentActivity } from '../../../types';

function activity(overrides: Partial<AgentActivity> = {}): AgentActivity {
  return {
    id: 1,
    agent_id: 8,
    team_id: 2,
    activity_type: 'llm_infer',
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

function tokenEl(wrapper: ReturnType<typeof mountItem>) {
  return wrapper.find('.agent-activity-item__tokens');
}

function segments(wrapper: ReturnType<typeof mountItem>) {
  return wrapper.findAll('.agent-activity-item__tokens-segment');
}

const ESTIMATED_CLASS = 'agent-activity-item__tokens-segment--estimated';

describe('#169 token 键名契约', () => {
  // ---------------- M7 新键读取 ----------------

  it('M7-a 真值三键（OpenAIUsage 同名）：↑ ↓ 都是真值色', () => {
    const wrapper = mountItem(activity({
      metadata: { prompt_tokens: 1200, completion_tokens: 200, total_tokens: 1400 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1200 ↓ 200');
    expect(segments(wrapper)).toHaveLength(2);
    expect(segments(wrapper)[0].classes()).not.toContain(ESTIMATED_CLASS);
    expect(segments(wrapper)[1].classes()).not.toContain(ESTIMATED_CLASS);
    // total_tokens 不参与显示
    expect(tokenEl(wrapper).text()).not.toContain('1400');
  });

  it('M7-b 估算两键：↑ ↓ 都带估算色', () => {
    const wrapper = mountItem(activity({
      status: 'started',
      metadata: { estimated_prompt_tokens: 1000, estimated_completion_tokens: 20 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1000 ↓ 20');
    expect(segments(wrapper)).toHaveLength(2);
    expect(segments(wrapper)[0].classes()).toContain(ESTIMATED_CLASS);
    expect(segments(wrapper)[1].classes()).toContain(ESTIMATED_CLASS);
  });

  it('M7-c 混色：输入真值 + 输出估算 ⇒ 只有 ↓ 段带估算色', () => {
    const wrapper = mountItem(activity({
      metadata: { prompt_tokens: 1200, estimated_completion_tokens: 200 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1200 ↓ 200');
    expect(segments(wrapper)[0].classes()).not.toContain(ESTIMATED_CLASS);
    expect(segments(wrapper)[1].classes()).toContain(ESTIMATED_CLASS);
  });

  // ---------------- M8 老数据不崩 ----------------

  it('M8 老键行（只有 estimated_prompt_tokens 可读）：只出 ↑ 段，不崩不出现假值', () => {
    // 这是**上线后的真实历史行形态**：老行里 `final_*` / `current_completion_tokens` 这些
    // 键名前端已不再读，唯一还能读到的就是没改名的 `estimated_prompt_tokens`。
    const wrapper = mountItem(activity({
      metadata: {
        estimated_prompt_tokens: 1000,
        current_completion_tokens: 20,
        final_prompt_tokens: 1200,
        final_completion_tokens: 44,
      },
    }));

    const text = tokenEl(wrapper).text();
    const parts = segments(wrapper);

    // 只出 ↑ 段：没有 ↓ 段元素（比「文本里没有 ↓」更强）
    expect(parts).toHaveLength(1);
    // 精确等于 `↑ 1000`：一并断言了「没出 ↓ 段」「没出 `↑ 0 ↓ 0`」「没出 undefined」
    // （早先这里写 `not.toContain('0')` 是错的——`1000` 本身就含 `0`，那是量具错不是被测对象错）
    expect(text).toBe('↑ 1000');
    expect(text).not.toContain('↓');
    expect(text).not.toContain('undefined');
    expect(text).not.toContain('NaN');
    // 唯一那段是估算色（它本来就是估算）
    expect(parts[0].classes()).toContain(ESTIMATED_CLASS);
  });

  it('M8-b 老键行完全读不到任何 token（只含被删除的 current_total_tokens）：不渲染、不抛错', () => {
    const wrapper = mountItem(activity({
      metadata: { current_total_tokens: 999, model: 'old-model' },
    }));

    expect(tokenEl(wrapper).exists()).toBe(false);
    expect(wrapper.text()).not.toContain('undefined');
    expect(wrapper.text()).not.toContain('NaN');
  });

  it('M8-c 老行 + 估算 completion 也在：↑ 估算 + ↓ 估算两段都渲染，不抛错', () => {
    // 历史行里 `current_completion_tokens` 现已不被读；但如果某行同时有
    // `estimated_completion_tokens`（#169 上线后新写的行）就该正常显示两段。
    const wrapper = mountItem(activity({
      metadata: { estimated_prompt_tokens: 1000, estimated_completion_tokens: 20 },
    }));

    expect(tokenEl(wrapper).text()).toBe('↑ 1000 ↓ 20');
    expect(segments(wrapper)).toHaveLength(2);
  });

  // ---------------- M10 `??` 不能被写成 `||` ----------------

  it('M10 真值为 0 时不能用估算顶替（`??` 不是 `||`）', () => {
    // ⚠️ 该形态当前后端不可达（`OpenAIUsage` 三字段默认 0 而非 None，`apply_usage` 无条件写入
    // 三个真值 ⇒ 真值 0 会同时带上 estimated 键只有「流中估算过、收尾 usage 回 0」这一种组合）。
    // 这里守的是**组件契约**：0 是真值，不是「没有值」。
    const wrapper = mountItem(activity({
      metadata: { prompt_tokens: 0, estimated_prompt_tokens: 1000 },
    }));

    const text = tokenEl(wrapper).text();
    // 用了 `||` 就会显示 `↑ 1000`，且被标成估算色
    expect(text).toBe('↑ 0');
    expect(text).not.toContain('1000');
    expect(segments(wrapper)[0].classes()).not.toContain(ESTIMATED_CLASS);
  });

  it('M10-b 真值 completion 为 0 时也不能被估算顶替', () => {
    const wrapper = mountItem(activity({
      metadata: { completion_tokens: 0, estimated_completion_tokens: 44 },
    }));

    const text = tokenEl(wrapper).text();
    expect(text).toBe('↓ 0');
    expect(text).not.toContain('44');
    expect(segments(wrapper)[0].classes()).not.toContain(ESTIMATED_CLASS);
  });
});
