import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import AgentTaskEventTimeline from '../AgentTaskEventTimeline.vue';
import i18n from '../../../i18n';
import type { AgentTaskEvent } from '../../../types';

function event(overrides: Partial<AgentTaskEvent> & Pick<AgentTaskEvent, 'id'>): AgentTaskEvent {
  return {
    id: overrides.id,
    task_id: overrides.task_id ?? 21,
    actor_id: overrides.actor_id ?? 3,
    from_status: overrides.from_status ?? null,
    to_status: overrides.to_status ?? null,
    content: overrides.content ?? '',
    created_at: overrides.created_at ?? '2026-10-27 10:00:00.000000',
    updated_at: overrides.updated_at ?? '2026-10-27 10:00:00.000000',
  };
}

function mountTimeline(events: AgentTaskEvent[], extra: Record<string, unknown> = {}) {
  return mount(AgentTaskEventTimeline, {
    props: {
      events,
      total: events.length,
      actorLabel: (id: number) => `Agent#${id}`,
      ...extra,
    },
    global: { plugins: [i18n] },
  });
}

describe('AgentTaskEventTimeline', () => {
  it('classifies the four event kinds from the status null-combination', () => {
    const wrapper = mountTimeline([
      event({ id: 1, to_status: 'TODO' }),                                   // create
      event({ id: 2, to_status: 'DONE', content: '【系统迁移自动生成】交付结论' }), // snapshot
      event({ id: 3, from_status: 'TODO', to_status: 'IN_PROGRESS', content: '开工' }), // transition
      event({ id: 4, content: '补充说明' }),                                  // comment
    ]);

    const kinds = wrapper.findAll('.task-timeline__item').map((item) => item.attributes('data-kind'));
    expect(kinds).toEqual(['create', 'snapshot', 'transition', 'comment']);
  });

  it('renders from → to only for transitions', () => {
    const wrapper = mountTimeline([
      event({ id: 1, from_status: 'IN_PROGRESS', to_status: 'REVIEWING', content: '提交验收' }),
      event({ id: 2, content: '仅评论' }),
    ]);

    const items = wrapper.findAll('.task-timeline__item');
    expect(items[0]?.findAll('.task-timeline__status')).toHaveLength(2);
    expect(items[1]?.findAll('.task-timeline__status')).toHaveLength(0);
  });

  it('strips the migration prefix from snapshot content', () => {
    const wrapper = mountTimeline([
      event({ id: 1, to_status: 'DONE', content: '【系统迁移自动生成】老数据结论' }),
    ]);

    const excerpt = wrapper.find('.task-timeline__excerpt').text();
    expect(excerpt).toBe('老数据结论');
    expect(excerpt).not.toContain('【系统迁移自动生成】');
  });

  it('collapses each event to one summary line by default', () => {
    const wrapper = mountTimeline([
      event({ id: 1, from_status: 'TODO', to_status: 'IN_PROGRESS', content: '开工' }),
    ]);

    const summary = wrapper.find('.task-timeline__summary');
    expect(summary.exists()).toBe(true);
    expect(summary.attributes('aria-expanded')).toBe('false');
    // 摘要行包含 content
    expect(summary.find('.task-timeline__excerpt').text()).toBe('开工');
    // 默认不渲染展开区
    expect(wrapper.find('.task-timeline__detail').exists()).toBe(false);
  });

  it('expands into a detail box on click and collapses again', async () => {
    const wrapper = mountTimeline([
      event({ id: 1, from_status: 'TODO', to_status: 'IN_PROGRESS', content: '第一行\n第二行' }),
    ]);

    await wrapper.find('.task-timeline__summary').trigger('click');

    const detail = wrapper.find('.task-timeline__detail');
    expect(detail.exists()).toBe(true);
    expect(detail.find('.task-timeline__detail-text').text()).toBe('第一行\n第二行');
    expect(wrapper.find('.task-timeline__summary').attributes('aria-expanded')).toBe('true');

    await wrapper.find('.task-timeline__summary').trigger('click');
    expect(wrapper.find('.task-timeline__detail').exists()).toBe(false);
  });

  it('resolves system and operator actors inside the component', () => {
    const wrapper = mountTimeline([
      event({ id: 1, actor_id: -2, to_status: 'TODO' }),
      event({ id: 2, actor_id: -1, content: '人工评论' }),
      event({ id: 3, actor_id: 42, content: '同事评论' }),
    ]);

    const actors = wrapper.findAll('.task-timeline__actor').map((node) => node.text());
    expect(actors).toEqual(['系统', '操作者', 'Agent#42']);
  });

  it('shows the load-more button only when more events exist, and emits on click', async () => {
    const wrapper = mountTimeline([event({ id: 5 })], { total: 12, hasMore: true });

    const more = wrapper.find('.task-timeline__more');
    expect(more.exists()).toBe(true);

    await more.trigger('click');
    expect(wrapper.emitted('load-more')).toHaveLength(1);
  });

  it('hides load-more when all events are loaded', () => {
    const wrapper = mountTimeline([event({ id: 5 })], { total: 1, hasMore: false });
    expect(wrapper.find('.task-timeline__more').exists()).toBe(false);
  });

  it('renders error state with retry emitting', async () => {
    const wrapper = mountTimeline([], { error: '事件加载失败。' });

    expect(wrapper.text()).toContain('事件加载失败。');
    await wrapper.find('.task-timeline__retry').trigger('click');
    expect(wrapper.emitted('retry')).toHaveLength(1);
  });

  it('renders empty state when there are no events', () => {
    const wrapper = mountTimeline([]);
    expect(wrapper.text()).toContain('暂无事件记录。');
  });
});
