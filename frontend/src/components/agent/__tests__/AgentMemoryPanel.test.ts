import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import AgentMemoryPanel from '../AgentMemoryPanel.vue';
import i18n from '../../../i18n';
import type { AgentMemory } from '../../../types';

const getAgentMemoriesMock = vi.fn();

vi.mock('../../../api', () => ({
  getAgentMemories: (...args: unknown[]) => getAgentMemoriesMock(...args),
}));

// 面板订阅 `agent_memory_changed` 重拉列表；测试里把订阅换成可手动触发的收集器。
const realtimeListeners: Array<(event: unknown) => void> = [];
vi.mock('../../../realtime/wsClient', () => ({
  subscribeRealtimeEvents: (listener: (event: unknown) => void) => {
    realtimeListeners.push(listener);
    return () => {
      const idx = realtimeListeners.indexOf(listener);
      if (idx >= 0) {
        realtimeListeners.splice(idx, 1);
      }
    };
  },
}));

function emitRealtime(event: unknown): void {
  for (const listener of [...realtimeListeners]) {
    listener(event);
  }
}

function createMemory(overrides: Partial<AgentMemory> = {}): AgentMemory {
  return {
    id: 12,
    team_id: 2,
    agent_id: 7,
    title: '方案输出规范',
    summary: '所有技术方案先给选型结论与权衡，再展开实现细节',
    content: '第一段正文\n第二段正文',
    star_level: 'summary',
    status: 'active',
    tags: ['输出风格', '交付规范'],
    created_at: '2026-11-06 10:00:00.000000',
    updated_at: '2026-11-06 10:24:00.000000',
    ...overrides,
  };
}

const SAMPLE: AgentMemory[] = [
  createMemory(),
  createMemory({
    id: 15,
    title: '存储层选型约束',
    summary: '第一阶段统一使用 SQLite，不引入外部向量库',
    content: '经团队讨论确认，为保证桌面端打包与部署轻量…',
    star_level: 'title',
    tags: [],
  }),
  createMemory({
    id: 18,
    title: '旧迁移方案',
    summary: '已废弃，改用方案 B',
    content: '旧方案正文',
    star_level: 'none',
    status: 'archived',
    tags: ['历史'],
  }),
];

function mountPanel(options: { open?: boolean; agentId?: number | null } = {}) {
  return mount(AgentMemoryPanel, {
    props: {
      open: options.open ?? true,
      agentId: options.agentId === undefined ? 7 : options.agentId,
    },
    global: { plugins: [i18n] },
  });
}

async function mountWithData(memories: AgentMemory[] = SAMPLE) {
  getAgentMemoriesMock.mockResolvedValue(memories);
  const wrapper = mountPanel();
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  getAgentMemoriesMock.mockReset();
  getAgentMemoriesMock.mockResolvedValue([]);
  realtimeListeners.length = 0;
});

describe('AgentMemoryPanel 筛选与列表', () => {
  it('默认只显示活跃记忆，归档记忆需切到「已归档」', async () => {
    const wrapper = await mountWithData();

    const cards = wrapper.findAll('.agent-memory-card');
    expect(cards).toHaveLength(2);
    expect(wrapper.text()).toContain('方案输出规范');
    expect(wrapper.text()).toContain('存储层选型约束');
    expect(wrapper.text()).not.toContain('旧迁移方案');
  });

  it('筛选按钮上带各分组计数', async () => {
    const wrapper = await mountWithData();
    const buttons = wrapper.findAll('.agent-memory-filter__btn').map((btn) => btn.text());

    expect(buttons[0]).toContain('活跃 (2)');
    expect(buttons[1]).toContain('已归档 (1)');
    expect(buttons[2]).toContain('全部 (3)');
  });

  it('切到「已归档」只显示归档记忆', async () => {
    const wrapper = await mountWithData();
    const archivedBtn = wrapper.findAll('.agent-memory-filter__btn')[1];
    await archivedBtn.trigger('click');

    const cards = wrapper.findAll('.agent-memory-card');
    expect(cards).toHaveLength(1);
    expect(cards[0].text()).toContain('旧迁移方案');
  });

  it('切到「全部」显示活跃与归档', async () => {
    const wrapper = await mountWithData();
    const allBtn = wrapper.findAll('.agent-memory-filter__btn')[2];
    await allBtn.trigger('click');

    expect(wrapper.findAll('.agent-memory-card')).toHaveLength(3);
  });

  it('无记忆时显示空状态文案', async () => {
    getAgentMemoriesMock.mockResolvedValue([]);
    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.find('.agent-activity-empty').exists()).toBe(true);
    expect(wrapper.text()).toContain('暂无长期记忆');
  });
});

describe('AgentMemoryPanel 星标与状态标识', () => {
  it('summary 级显示黄金星，title 级显示白银星，普通记忆无徽标', async () => {
    const wrapper = await mountWithData();
    const cards = wrapper.findAll('.agent-memory-card');
    const summaryStar = cards[0].find('.agent-memory-card__star');
    const titleStar = cards[1].find('.agent-memory-card__star');

    // 徽标本身只渲染星星图标，等级文字保留在 title / aria-label 中。
    expect(summaryStar.find('i.fa-star').exists()).toBe(true);
    expect(summaryStar.attributes('data-star-level')).toBe('summary');
    expect(summaryStar.attributes('title')).toBe('Agent 已加星：标题和概要会注入提示词');
    expect(summaryStar.attributes('aria-label')).toBe('Agent 已加星：标题和概要会注入提示词');

    expect(titleStar.find('i.fa-star').exists()).toBe(true);
    expect(titleStar.attributes('data-star-level')).toBe('title');
    expect(titleStar.attributes('title')).toBe('Agent 已加星：标题会注入提示词');
    expect(titleStar.attributes('aria-label')).toBe('Agent 已加星：标题会注入提示词');

    // 普通记忆（none）不渲染星标徽标
    const plain = await mountWithData([createMemory({ id: 30, star_level: 'none', title: '普通记忆' })]);
    expect(plain.find('.agent-memory-card__star').exists()).toBe(false);
  });

  it('归档记忆显示「已归档」标签并带 is-archived 样式', async () => {
    const wrapper = await mountWithData();
    const archivedBtn = wrapper.findAll('.agent-memory-filter__btn')[1];
    await archivedBtn.trigger('click');

    const card = wrapper.find('.agent-memory-card');
    expect(card.classes()).toContain('is-archived');
    expect(card.find('.agent-memory-card__archived').text()).toBe('已归档');
  });

  it('按后端给的小写 star_level / status 渲染（不是大写 name）', async () => {
    const wrapper = await mountWithData();
    const card = wrapper.find('.agent-memory-card');

    expect(card.attributes('data-star-level')).toBe('summary');
    expect(card.attributes('data-status')).toBe('active');
  });
});

describe('AgentMemoryPanel 星标配额统计', () => {
  it('显示「星标 N/12 · 概要 M/6」固定格式', async () => {
    const wrapper = await mountWithData();
    expect(wrapper.find('.agent-memory-stars').text()).toBe('星标 2/12 · 概要 1/6');
  });

  it('统计口径是全部活跃记忆，不受当前筛选影响', async () => {
    const wrapper = await mountWithData();

    const archivedBtn = wrapper.findAll('.agent-memory-filter__btn')[1];
    await archivedBtn.trigger('click');

    // 切到「已归档」后星标数仍是活跃记忆的占用
    expect(wrapper.find('.agent-memory-stars').text()).toBe('星标 2/12 · 概要 1/6');
  });

  it('归档记忆不占用星标额度（即使后端残留了 star_level）', async () => {
    // 构造脏数据：归档却 still summary（正常路径后端会剥离，这里验证前端兜底口径）
    const wrapper = await mountWithData([
      createMemory({ id: 1, star_level: 'none' }),
      createMemory({ id: 2, star_level: 'summary', status: 'archived' }),
    ]);

    expect(wrapper.find('.agent-memory-stars').text()).toBe('星标 0/12 · 概要 0/6');
  });
});

describe('AgentMemoryPanel 正文展开', () => {
  it('默认不展示正文，点击「展开正文」后原地展开', async () => {
    const wrapper = await mountWithData();
    const card = wrapper.find('.agent-memory-card');

    expect(card.find('.agent-memory-card__content').exists()).toBe(false);

    await card.find('.agent-memory-card__toggle').trigger('click');

    const content = card.find('.agent-memory-card__content');
    expect(content.exists()).toBe(true);
    expect(content.text()).toContain('第一段正文');
  });

  it('再次点击可收起', async () => {
    const wrapper = await mountWithData();
    const card = wrapper.find('.agent-memory-card');

    await card.find('.agent-memory-card__toggle').trigger('click');
    expect(card.find('.agent-memory-card__content').exists()).toBe(true);

    await card.find('.agent-memory-card__toggle').trigger('click');
    expect(card.find('.agent-memory-card__content').exists()).toBe(false);
  });

  it('展开状态按记忆 id 独立，互不影响', async () => {
    const wrapper = await mountWithData();
    const cards = wrapper.findAll('.agent-memory-card');

    await cards[0].find('.agent-memory-card__toggle').trigger('click');

    expect(cards[0].find('.agent-memory-card__content').exists()).toBe(true);
    expect(cards[1].find('.agent-memory-card__content').exists()).toBe(false);
  });
});

describe('AgentMemoryPanel 数据加载', () => {
  it('打开时按 agentId 拉取（含归档）', async () => {
    await mountWithData();
    expect(getAgentMemoriesMock).toHaveBeenCalledWith(7, true);
  });

  it('未打开或无 agentId 时不请求', async () => {
    mountPanel({ open: false });
    await flushPromises();
    expect(getAgentMemoriesMock).not.toHaveBeenCalled();

    getAgentMemoriesMock.mockClear();
    mountPanel({ agentId: null });
    await flushPromises();
    expect(getAgentMemoriesMock).not.toHaveBeenCalled();
  });

  it('加载失败显示错误提示', async () => {
    getAgentMemoriesMock.mockRejectedValue(new Error('boom'));
    const wrapper = mountPanel();
    await flushPromises();

    expect(wrapper.find('.error-banner').text()).toBe('加载 Agent 记忆失败。');
  });

  it('收到本 Agent 的记忆变更推送后重拉列表（弹窗开着时也能刷新）', async () => {
    // need_fix_1.md P1-3 b 段：面板 v-show 不销毁，弹窗开着时没有别的刷新路径。
    const wrapper = await mountWithData();
    expect(getAgentMemoriesMock).toHaveBeenCalledTimes(1);

    getAgentMemoriesMock.mockResolvedValue([
      ...SAMPLE,
      createMemory({ id: 42, title: '新增的记忆' }),
    ]);
    emitRealtime({ type: 'agent_memory_changed', teamId: 2, agentId: 7, memoryId: 42, action: 'created' });
    await flushPromises();

    expect(getAgentMemoriesMock).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain('新增的记忆');
  });

  it('忽略其他 Agent 的记忆变更', async () => {
    await mountWithData();
    expect(getAgentMemoriesMock).toHaveBeenCalledTimes(1);

    emitRealtime({ type: 'agent_memory_changed', teamId: 2, agentId: 8, memoryId: 42, action: 'created' });
    await flushPromises();

    expect(getAgentMemoriesMock).toHaveBeenCalledTimes(1);
  });

  it('面板关闭时不再订阅，重新打开后恢复', async () => {
    getAgentMemoriesMock.mockResolvedValue(SAMPLE);
    const wrapper = mountPanel({ open: false });
    await flushPromises();
    expect(realtimeListeners).toHaveLength(0);

    await wrapper.setProps({ open: true });
    await flushPromises();
    expect(realtimeListeners).toHaveLength(1);

    await wrapper.setProps({ open: false });
    await flushPromises();
    expect(realtimeListeners).toHaveLength(0);
  });

  it('向父组件同步当前可见条数', async () => {
    const wrapper = await mountWithData();

    const emitted = wrapper.emitted('count-change') ?? [];
    expect(emitted[emitted.length - 1]).toEqual([2]);

    const allBtn = wrapper.findAll('.agent-memory-filter__btn')[2];
    await allBtn.trigger('click');
    const afterAll = wrapper.emitted('count-change') ?? [];
    expect(afterAll[afterAll.length - 1]).toEqual([3]);
  });
});
