import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { scheduleNotRunningReason, scheduleState, totalMessageCount } from '../../appUiState';
import type { AgentActivity, AgentContextUsage, AgentInfo, AgentTask, AgentTaskEvent, MessageInfo, RoomState } from '../../types';
import {
  appendTaskEvent,
  applyRealtimeEvent,
  clearRuntimeStore,
  getAgentActivities,
  getAgentContextUsage,
  getAgentExecEnv,
  getAgentStatus,
  getTeamAgents,
  getRoomMessageHistoryState,
  getRoomMessages,
  getTaskEvents,
  getTaskEventsTotal,
  getTeamRooms,
  getTeamTasks,
  mergeTaskEvents,
  prependTaskEvents,
  seedAgentContextUsage,
  seedRoomMessages,
  seedTeamAgents,
  seedTeamRooms,
  setActiveRealtimeContext,
  setTaskEvents,
} from '../runtimeStore';

function createAgent(overrides: Partial<AgentInfo>): AgentInfo {
  return {
    id: 1,
    name: 'Alpha',
    i18n: {},
    model: '',
    status: 'idle',
    ...overrides,
  };
}

function createRoom(overrides: Partial<RoomState>): RoomState {
  return {
    room_id: 3,
    room_name: 'general',
    i18n: {},
    room_type: 'group',
    state: 'idle',
    need_scheduling: false,
    agents: [1],
    current_turn_agent_id: null,
    preview: '旧预览',
    unread: 0,
    ...overrides,
  };
}

function createMessage(overrides: Partial<MessageInfo>): MessageInfo {
  return {
    db_id: 1,
    sender_id: -1,
    sender_display_name: 'OPERATOR',
    content: 'message',
    time: '2026-05-04 22:24:00',
    seq: 0,
    insert_immediately: false,
    ...overrides,
  };
}

const LIMIT = 114688;

function createUsage(overrides: Partial<AgentContextUsage> = {}): AgentContextUsage {
  const used = overrides.used_tokens ?? 40000;
  const limit = overrides.limit_tokens ?? LIMIT;
  return {
    used_tokens: used,
    limit_tokens: limit,
    compact_trigger_tokens: overrides.compact_trigger_tokens ?? 97484,
    context_window_tokens: overrides.context_window_tokens ?? 131072,
    ratio: overrides.ratio ?? used / limit,
  };
}

function createActivity(overrides: Partial<AgentActivity> = {}): AgentActivity {
  return {
    id: 10,
    agent_id: 1,
    team_id: 1,
    activity_type: 'llm_infer',
    status: 'started',
    title: '推理',
    detail: '',
    error_message: null,
    started_at: '2026-05-06T10:00:00',
    finished_at: null,
    duration_ms: null,
    metadata: { model: 'test-model' },
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

describe('runtimeStore realtime events', () => {
  beforeEach(() => {
    clearRuntimeStore();
  });

  afterEach(() => {
    clearRuntimeStore();
  });

  it('updates preview and unread count for inactive rooms on message events', () => {
    seedTeamAgents(2, [createAgent({ id: 5, name: 'Alice' })]);
    seedTeamRooms(2, [createRoom({ room_id: 9, agents: [5], preview: '旧内容' })]);

    applyRealtimeEvent({
      type: 'message',
      teamId: 2,
      roomId: 9,
      roomName: 'general',
      message: createMessage({
        db_id: 11,
        sender_id: 5,
        sender_display_name: '',
        content: '新的进展',
        time: '2026-05-04 22:30:00',
      }),
    });

    const room = getTeamRooms(2)[0];
    expect(room?.preview).toBe('Alice: 新的进展');
    expect(room?.unread).toBe(1);
    expect(getRoomMessages(9)).toHaveLength(1);
  });

  it('keeps unread at zero and syncs total count for the active room', () => {
    seedTeamRooms(2, [createRoom({ room_id: 4, unread: 3 })]);
    seedRoomMessages(4, [createMessage({ db_id: 21, content: '已有消息' })]);
    setActiveRealtimeContext(2, 4);

    applyRealtimeEvent({
      type: 'message',
      teamId: 2,
      roomId: 4,
      roomName: 'general',
      message: createMessage({
        db_id: 22,
        content: '最新消息',
        time: '2026-05-04 22:35:00',
      }),
    });

    const room = getTeamRooms(2)[0];
    expect(room?.unread).toBe(0);
    expect(totalMessageCount.value).toBe(2);
    expect(getRoomMessages(4).map((message) => message.db_id)).toEqual([21, 22]);
  });

  it('re-sorts messages by seq when a message_changed event arrives', () => {
    seedRoomMessages(3, [
      createMessage({ db_id: 27, content: '测试27', seq: 0, time: '2026-05-04 22:24:29' }),
      // seq 由后端插入即分配，缺失时归一化为 0 并靠 db_id 兜底排序。
      createMessage({ db_id: 28, content: '测试28', seq: 0, time: '2026-05-04 22:24:41' }),
    ]);

    applyRealtimeEvent({
      type: 'message_changed',
      teamId: 2,
      roomId: 3,
      roomName: '小马哥',
      message: createMessage({ db_id: 28, content: '测试28', seq: 1, time: '2026-05-04 22:24:41' }),
    });

    const messages = getRoomMessages(3);
    expect(messages.map((message) => message.db_id)).toEqual([27, 28]);
    expect(messages.map((message) => message.seq)).toEqual([0, 1]);
  });

  it('updates agent and room state from realtime status events', () => {
    seedTeamAgents(2, [createAgent({ id: 8, status: 'idle' })]);
    seedTeamRooms(2, [createRoom({ room_id: 6, state: 'idle', need_scheduling: false })]);

    applyRealtimeEvent({
      type: 'agent_status',
      teamId: 2,
      agentId: 8,
      agentName: 'Alpha',
      status: 'active',
    });
    applyRealtimeEvent({
      type: 'room_status',
      teamId: 2,
      roomId: 6,
      state: 'scheduling',
      needScheduler: true,
      currentTurnAgentId: 8,
    });

    expect(getAgentStatus(8)).toBe('active');
    const room = getTeamRooms(2)[0];
    expect(room?.state).toBe('scheduling');
    expect(room?.need_scheduling).toBe(true);
    expect(room?.current_turn_agent_id).toBe(8);
  });

  // ─── agent_container：执行环境内存态 ───────────────────────────────────
  describe('agent_container events', () => {
    it('takes exec env from the loaded agent list as the initial value', () => {
      seedTeamAgents(2, [createAgent({ id: 8, exec_env: 'container', current_container_id: 'abc123def456' })]);

      expect(getAgentExecEnv(8)).toEqual({ execEnv: 'container', currentContainerId: 'abc123def456' });
    });

    it('treats a missing exec_env as host (unloaded agents are always host)', () => {
      seedTeamAgents(2, [createAgent({ id: 8 })]);

      expect(getAgentExecEnv(8)).toEqual({ execEnv: 'host', currentContainerId: null });
    });

    it('records the env even when the team list is not loaded yet', () => {
      // 先进容器、后加载列表：若只在列表存在时才记，徽标会停在旧值。
      applyRealtimeEvent({
        type: 'agent_container',
        teamId: 2,
        agentId: 8,
        agentName: 'Alpha',
        execEnv: 'container',
        currentContainerId: 'abc123def456',
        containerName: 'agent-t2-alpha-7',
      });

      expect(getAgentExecEnv(8)).toEqual({ execEnv: 'container', currentContainerId: 'abc123def456' });
    });

    it('updates both the env store and the agent list entry', () => {
      seedTeamAgents(2, [createAgent({ id: 8, exec_env: 'host', current_container_id: null })]);

      applyRealtimeEvent({
        type: 'agent_container',
        teamId: 2,
        agentId: 8,
        agentName: 'Alpha',
        execEnv: 'container',
        currentContainerId: 'abc123def456',
        containerName: 'agent-t2-alpha-7',
      });

      expect(getAgentExecEnv(8)).toEqual({ execEnv: 'container', currentContainerId: 'abc123def456' });
      const agent = getTeamAgents(2).find((item) => item.id === 8);
      expect(agent?.exec_env).toBe('container');
      expect(agent?.current_container_id).toBe('abc123def456');
      expect(agent?.current_container_name).toBe('agent-t2-alpha-7');
    });

    it('clears the container id when the agent exits back to host', () => {
      seedTeamAgents(2, [createAgent({ id: 8, exec_env: 'container', current_container_id: 'abc123def456' })]);

      applyRealtimeEvent({
        type: 'agent_container',
        teamId: 2,
        agentId: 8,
        agentName: 'Alpha',
        execEnv: 'host',
        currentContainerId: null,
        containerName: null,
      });

      expect(getAgentExecEnv(8)).toEqual({ execEnv: 'host', currentContainerId: null });
      const agent = getTeamAgents(2).find((item) => item.id === 8);
      expect(agent?.current_container_id).toBeNull();
      expect(agent?.current_container_name).toBeNull();
    });

    it('defaults to host for unknown agent ids', () => {
      expect(getAgentExecEnv(999)).toEqual({ execEnv: 'host', currentContainerId: null });
      expect(getAgentExecEnv(null)).toEqual({ execEnv: 'host', currentContainerId: null });
    });

    it('does not leave stale env behind after clearing the store', () => {
      seedTeamAgents(2, [createAgent({ id: 8, exec_env: 'container', current_container_id: 'abc123def456' })]);
      clearRuntimeStore();

      expect(getAgentExecEnv(8)).toEqual({ execEnv: 'host', currentContainerId: null });
    });
  });

  // ─── agent_context_usage：上下文用量内存态 ──────────────────────────────
  describe('agent_context_usage events', () => {
    it('seeds from the detail snapshot', () => {
      seedAgentContextUsage(8, createUsage({ used_tokens: 40000 }));

      expect(getAgentContextUsage(8)).toEqual(createUsage({ used_tokens: 40000 }));
    });

    it('returns null (not a zero-filled object) when nothing is known', () => {
      // 语义是「不知道」：组件据此整条不渲染。若返回全 0 对象，进度条会画 0%，
      // 那是在断言「上下文是空的」——而它只是没算出来。
      expect(getAgentContextUsage(999)).toBeNull();
      expect(getAgentContextUsage(null)).toBeNull();
    });

    it('deletes the key when seeding null (后端明确说「算不出来」)', () => {
      // null 是后端明确表态「此刻算不出用量」（模型配置解析失败 / limit 非正 /
      // Agent 未装载）。进度条该消失：继续显示上一次的数值，等于在断言
      // 「上下文还是那么多」，而实际是「不知道」。
      seedAgentContextUsage(8, createUsage({ used_tokens: 40000 }));
      seedAgentContextUsage(8, null);
      expect(getAgentContextUsage(8)).toBeNull();
    });

    it('keeps the existing value when seeding undefined (字段缺省 ≠ 失效)', () => {
      // undefined 是「这条响应里没有该字段」（老后端 / 详情先到推送后到），
      // 不代表先前推送的值已失效。删掉会让刚显示的进度条突然消失。
      seedAgentContextUsage(8, createUsage({ used_tokens: 40000 }));
      seedAgentContextUsage(8, undefined);
      expect(getAgentContextUsage(8)).toEqual(createUsage({ used_tokens: 40000 }));
    });

    it('seeding null for an unknown agent is a no-op, not an error', () => {
      // 从没写过数据的 agent 收到 null：解构删除是幂等的，不能抛、也不能凭空造 key。
      seedAgentContextUsage(8, null);
      expect(getAgentContextUsage(8)).toBeNull();
    });

    it('ignores seeding for negative / null agent ids', () => {
      // 操作者与 SYSTEM 是负 id，永远不该有上下文用量
      seedAgentContextUsage(-1, createUsage());
      seedAgentContextUsage(null, createUsage());

      expect(getAgentContextUsage(-1)).toBeNull();
      expect(getAgentContextUsage(null)).toBeNull();
    });

    it('advances from realtime events without touching the team agent list', () => {
      seedTeamAgents(2, [createAgent({ id: 8 })]);
      seedAgentContextUsage(8, createUsage({ used_tokens: 40000 }));

      const next = createUsage({ used_tokens: 90000, ratio: 90000 / 114688 });
      applyRealtimeEvent({ type: 'agent_context_usage', teamId: 2, agentId: 8, contextUsage: next });

      expect(getAgentContextUsage(8)).toEqual(next);
      // 用量不属于 AgentInfo：不回写列表项，避免「列表接口返回该字段」的错觉
      const agent = getTeamAgents(2).find((item) => item.id === 8) as unknown as Record<string, unknown>;
      expect(agent.context_usage).toBeUndefined();
    });

    it('records usage even when the team list is not loaded yet', () => {
      applyRealtimeEvent({
        type: 'agent_context_usage',
        teamId: 2,
        agentId: 8,
        contextUsage: createUsage({ used_tokens: 40000 }),
      });

      expect(getAgentContextUsage(8)).toEqual(createUsage({ used_tokens: 40000 }));
    });

    it('does not leave stale usage behind after clearing the store', () => {
      seedAgentContextUsage(8, createUsage());
      clearRuntimeStore();

      expect(getAgentContextUsage(8)).toBeNull();
    });
  });

  it('keeps the activity array stable when a realtime activity changes status', () => {    applyRealtimeEvent({
      type: 'agent_activity',
      agentId: 1,
      activity: createActivity(),
    });

    const beforeUpdate = getAgentActivities(1);
    const beforeActivity = beforeUpdate[0];

    applyRealtimeEvent({
      type: 'agent_activity',
      agentId: 1,
      activity: createActivity({
        status: 'succeeded',
        finished_at: '2026-05-06T10:00:03',
        duration_ms: 3000,
        metadata: {
          model: 'test-model',
          total_tokens: 128,
        },
      }),
    });

    const afterUpdate = getAgentActivities(1);

    expect(afterUpdate).toBe(beforeUpdate);
    expect(afterUpdate).toHaveLength(1);
    expect(afterUpdate[0]).toBe(beforeActivity);
    expect(afterUpdate[0]).toMatchObject({
      id: 10,
      status: 'succeeded',
      duration_ms: 3000,
      metadata: {
        total_tokens: 128,
      },
    });
  });

  it('adds rooms only once and syncs schedule state events', () => {
    seedTeamRooms(2, [createRoom({ room_id: 6 })]);

    const newRoom = createRoom({
      room_id: 7,
      room_name: 'ops',
      preview: '初始预览',
      unread: 0,
    });

    applyRealtimeEvent({
      type: 'room_added',
      teamId: 2,
      room: newRoom,
    });
    applyRealtimeEvent({
      type: 'room_added',
      teamId: 2,
      room: newRoom,
    });
    applyRealtimeEvent({
      type: 'schedule_state',
      scheduleState: 'blocked',
      notRunningReason: 'manual_pause',
    });

    expect(getTeamRooms(2).map((room) => room.room_id)).toEqual([6, 7]);
    expect(scheduleState.value).toBe('blocked');
    expect(scheduleNotRunningReason.value).toBe('manual_pause');
  });
});

describe('runtimeStore message history state', () => {
  beforeEach(() => {
    clearRuntimeStore();
  });

  afterEach(() => {
    clearRuntimeStore();
  });

  it('returns default history state for unknown room', () => {
    expect(getRoomMessageHistoryState(null)).toEqual({ hasMoreHistory: false, loadingHistory: false });
    expect(getRoomMessageHistoryState(999)).toEqual({ hasMoreHistory: false, loadingHistory: false });
  });

  it('seedRoomMessages stores hasMoreHistory flag', () => {
    seedRoomMessages(1, [createMessage({ db_id: 10 })], { hasMoreHistory: true });
    expect(getRoomMessageHistoryState(1).hasMoreHistory).toBe(true);
    expect(getRoomMessages(1)).toHaveLength(1);
  });

  it('seedRoomMessages without hasMoreHistory defaults to false', () => {
    seedRoomMessages(1, [createMessage({ db_id: 10 })]);
    expect(getRoomMessageHistoryState(1).hasMoreHistory).toBe(false);
  });

  it('seedRoomMessages with preserveExisting merges messages and preserves hasMoreHistory', () => {
    seedRoomMessages(1, [createMessage({ db_id: 20, content: 'newer' })], { hasMoreHistory: true });
    seedRoomMessages(1, [createMessage({ db_id: 10, content: 'older' })], {
      preserveExisting: true,
      hasMoreHistory: false,
    });

    const messages = getRoomMessages(1);
    expect(messages.map((m) => m.db_id)).toEqual([10, 20]);
    expect(getRoomMessageHistoryState(1).hasMoreHistory).toBe(false);
  });

  it('realtime message event preserves hasMoreHistory', () => {
    seedRoomMessages(5, [createMessage({ db_id: 30 })], { hasMoreHistory: true });

    applyRealtimeEvent({
      type: 'message',
      teamId: 1,
      roomId: 5,
      roomName: 'test',
      message: createMessage({ db_id: 31, content: 'new', time: '2026-05-04 22:40:00' }),
    });

    expect(getRoomMessageHistoryState(5).hasMoreHistory).toBe(true);
    expect(getRoomMessages(5)).toHaveLength(2);
  });

  it('merges task events by id and keeps ascending order', () => {
    const merged = mergeTaskEvents(
      [taskEvent(2), taskEvent(1)],
      [taskEvent(3), taskEvent(2, '覆盖')],
    );

    expect(merged.map((event) => event.id)).toEqual([1, 2, 3]);
    expect(merged[1]?.content).toBe('覆盖');
  });

  it('appends realtime task event idempotently and tracks total', () => {
    setTaskEvents(21, [taskEvent(1), taskEvent(2)], 2);

    appendTaskEvent(21, taskEvent(3));
    appendTaskEvent(21, taskEvent(3)); // 重复投递（同时保留旧主题双发场景）

    expect(getTaskEvents(21).map((event) => event.id)).toEqual([1, 2, 3]);
    expect(getTaskEventsTotal(21)).toBe(3);
  });

  it('prepends earlier page without losing pushed events', () => {
    setTaskEvents(21, [taskEvent(5), taskEvent(6)], 6);

    prependTaskEvents(21, [taskEvent(3), taskEvent(4)], 6);

    expect(getTaskEvents(21).map((event) => event.id)).toEqual([3, 4, 5, 6]);
    expect(getTaskEventsTotal(21)).toBe(6);
  });

  it('applyRealtimeEvent stores task_event_appended in both task list and timeline', () => {
    applyRealtimeEvent({
      type: 'task_event_appended',
      teamId: 1,
      task: createTask({ id: 21, status: 'IN_PROGRESS' }),
      event: taskEvent(9),
    });

    expect(getTeamTasks(1).map((task) => task.id)).toContain(21);
    expect(getTaskEvents(21).map((event) => event.id)).toEqual([9]);
  });
});

function taskEvent(id: number, content = '内容'): AgentTaskEvent {
  return {
    id,
    task_id: 21,
    actor_id: 3,
    from_status: null,
    to_status: null,
    content,
    created_at: '2026-10-27 10:00:00.000000',
    updated_at: '2026-10-27 10:00:00.000000',
  };
}

function createTask(overrides: Partial<AgentTask> & Pick<AgentTask, 'id'>): AgentTask {
  return {
    id: overrides.id,
    team_id: overrides.team_id ?? 1,
    title: overrides.title ?? '任务',
    description: overrides.description ?? '',
    assignee_id: overrides.assignee_id ?? 3,
    creator_id: overrides.creator_id ?? 1,
    reviewer_id: overrides.reviewer_id ?? null,
    status: overrides.status ?? 'TODO',
    priority: overrides.priority ?? 'NORMAL',
    parent_id: null,
    depends_on: [],
    room_id: null,
    created_at: null,
    updated_at: null,
  };
}
