import { describe, expect, it } from 'vitest';
import { normalizeWsEventPayload } from '../eventNormalizer';

describe('normalizeWsEventPayload', () => {
  it('normalizes room_status with current_turn_agent_id', () => {
    const event = normalizeWsEventPayload({
      event: 'room_status',
      gt_room: {
        id: 11,
        team_id: 7,
        name: 'general',
      },
      state: 'SCHEDULING',
      need_scheduling: true,
      current_turn_agent_id: 42,
    });

    expect(event).toEqual({
      type: 'room_status',
      teamId: 7,
      roomId: 11,
      state: 'scheduling',
      needScheduler: true,
      currentTurnAgentId: 42,
    });
  });

  describe('agent_memory_changed', () => {
    it('normalizes team/agent/memory identity and action', () => {
      const event = normalizeWsEventPayload({
        event: 'agent_memory_changed',
        team_id: 2,
        agent_id: 7,
        memory_id: 31,
        action: 'created',
      });

      expect(event).toEqual({
        type: 'agent_memory_changed',
        teamId: 2,
        agentId: 7,
        memoryId: 31,
        action: 'created',
      });
    });

    it('drops the event when agent or team identity is invalid', () => {
      // 脏 payload 不能进业务分发：面板按 agentId 比对，脏 id 会误触发别的 Agent 重拉。
      expect(normalizeWsEventPayload({
        event: 'agent_memory_changed', team_id: 2, agent_id: 0, memory_id: 31,
      })).toBeNull();

      expect(normalizeWsEventPayload({
        event: 'agent_memory_changed', team_id: -1, agent_id: 7, memory_id: 31,
      })).toBeNull();
    });

    it('keeps memory_id numeric even when the payload omits it', () => {
      // 归档/删除类变更可能没有具体 id；这时仍要推给面板，只是 memoryId 归 0。
      const event = normalizeWsEventPayload({
        event: 'agent_memory_changed', team_id: 2, agent_id: 7, action: 'updated',
      });

      expect(event).toMatchObject({ type: 'agent_memory_changed', memoryId: 0 });
    });
  });

  it('normalizes message_changed db_id from id fallback', () => {
    const event = normalizeWsEventPayload({
      event: 'message_changed',
      gt_room: {
        id: 11,
        team_id: 7,
        name: 'general',
      },
      gt_message: {
        id: 28,
        sender_id: -1,
        sender_display_name: 'OPERATOR',
        content: '测试',
        send_time: '2026-05-24 00:05:57',
        seq: 1,
        insert_immediately: false,
      },
    });

    expect(event).toEqual({
      type: 'message_changed',
      teamId: 7,
      roomId: 11,
      roomName: 'general',
      message: {
        db_id: 28,
        sender_id: -1,
        sender_display_name: 'OPERATOR',
        content: '测试',
        time: '2026-05-24 00:05:57',
        seq: 1,
        insert_immediately: false,
      },
    });
  });

  it('normalizes task_event_appended with business event under task_event key', () => {
    const event = normalizeWsEventPayload({
      event: 'task_event_appended',
      team_id: 7,
      task: {
        id: 21,
        team_id: 7,
        title: '接口联调',
        description: '',
        assignee_id: 3,
        creator_id: 1,
        reviewer_id: 2,
        status: 'IN_PROGRESS',
        priority: 'HIGH',
        parent_id: null,
        depends_on: [],
        room_id: null,
        created_at: '2026-10-27 10:00:00.000000',
        updated_at: '2026-10-27 10:05:00.000000',
      },
      task_event: {
        id: 88,
        task_id: 21,
        actor_id: -2,
        from_status: 'TODO',
        to_status: 'IN_PROGRESS',
        content: '执行人认领，系统自动置为 IN_PROGRESS',
        created_at: '2026-10-27 10:05:00.000000',
      },
    });

    expect(event?.type).toBe('task_event_appended');
    if (event?.type !== 'task_event_appended') {
      throw new Error('unexpected event type');
    }
    expect(event.teamId).toBe(7);
    expect(event.task.reviewer_id).toBe(2);
    expect(event.event.id).toBe(88);
    expect(event.event.from_status).toBe('TODO');
    expect(event.event.to_status).toBe('IN_PROGRESS');
  });

  it('drops task_event_appended when the business event is malformed', () => {
    const event = normalizeWsEventPayload({
      event: 'task_event_appended',
      task: { id: 21, team_id: 7, title: 'x', assignee_id: 3, creator_id: 1 },
      task_event: { id: 'bad' },
    });

    expect(event).toBeNull();
  });

  // ─── agent_container：执行环境事件（payload 与 agent_status 同一套 gt_agent 约定）───
  describe('agent_container', () => {
    it('reads identity from the full gt_agent object, not a bare id', () => {
      const event = normalizeWsEventPayload({
        event: 'agent_container',
        gt_agent: { id: 8, team_id: 2, name: '李哥' },
        exec_env: 'container',
        current_container_id: 'a1b2c3d4e5f6',
      });

      expect(event).toEqual({
        type: 'agent_container',
        teamId: 2,
        agentId: 8,
        agentName: '李哥',
        execEnv: 'container',
        currentContainerId: 'a1b2c3d4e5f6',
      });
    });

    it('keeps host env with null container id after exiting a container', () => {
      const event = normalizeWsEventPayload({
        event: 'agent_container',
        gt_agent: { id: 8, team_id: 2, name: '李哥' },
        exec_env: 'host',
        current_container_id: null,
      });

      expect(event).toMatchObject({ type: 'agent_container', execEnv: 'host', currentContainerId: null });
    });

    it('does not leak a container id when env is host (dirty payload)', () => {
      // 宿主态却带了 container_id：若照单全收，前端会高亮一行其实没在用的容器。
      const event = normalizeWsEventPayload({
        event: 'agent_container',
        gt_agent: { id: 8, team_id: 2, name: '李哥' },
        exec_env: 'host',
        current_container_id: 'deadbeef1234',
      });

      expect(event).toMatchObject({ execEnv: 'host', currentContainerId: null });
    });

    it('falls back to host for unknown exec_env values', () => {
      const event = normalizeWsEventPayload({
        event: 'agent_container',
        gt_agent: { id: 8, team_id: 2, name: '李哥' },
        exec_env: 'PODMAN',
        current_container_id: null,
      });

      expect(event).toMatchObject({ execEnv: 'host' });
    });

    it('drops the event when gt_agent is missing or has invalid ids', () => {
      expect(normalizeWsEventPayload({
        event: 'agent_container',
        exec_env: 'container',
      })).toBeNull();

      expect(normalizeWsEventPayload({
        event: 'agent_container',
        gt_agent: { id: 0, team_id: 2 },
        exec_env: 'container',
      })).toBeNull();

      expect(normalizeWsEventPayload({
        event: 'agent_container',
        gt_agent: { id: 8, team_id: -1 },
        exec_env: 'container',
      })).toBeNull();
    });
  });

  // ─── agent_context_usage：扁平 payload（没有 gt_agent 对象）───────────────
  describe('agent_context_usage', () => {
    const validUsage = {
      used_tokens: 40000,
      limit_tokens: 114688,
      compact_trigger_tokens: 97484,
      context_window_tokens: 131072,
      ratio: 0.3487,
    };

    it('normalizes the flat payload without a gt_agent wrapper', () => {
      const event = normalizeWsEventPayload({
        event: 'agent_context_usage',
        team_id: 2,
        agent_id: 7,
        context_usage: validUsage,
      });

      expect(event).toEqual({
        type: 'agent_context_usage',
        teamId: 2,
        agentId: 7,
        contextUsage: validUsage,
      });
    });

    it('keeps ratio above 1 untouched (over-limit is real diagnostic info)', () => {
      const event = normalizeWsEventPayload({
        event: 'agent_context_usage',
        team_id: 2,
        agent_id: 7,
        context_usage: { ...validUsage, used_tokens: 200000, ratio: 200000 / 114688 },
      });

      expect(event?.type).toBe('agent_context_usage');
      if (event?.type !== 'agent_context_usage') {
        throw new Error('unexpected event type');
      }
      expect(event.contextUsage.ratio).toBeGreaterThan(1);
    });

    it('drops the event when team/agent ids are missing or non-positive', () => {
      expect(normalizeWsEventPayload({
        event: 'agent_context_usage',
        agent_id: 7,
        context_usage: validUsage,
      })).toBeNull();

      expect(normalizeWsEventPayload({
        event: 'agent_context_usage',
        team_id: 2,
        agent_id: 0,
        context_usage: validUsage,
      })).toBeNull();

      // 负 id（操作者 / SYSTEM）不该有上下文用量
      expect(normalizeWsEventPayload({
        event: 'agent_context_usage',
        team_id: 2,
        agent_id: -1,
        context_usage: validUsage,
      })).toBeNull();
    });

    it('drops the whole event when any usage field is dirty', () => {
      // 半张脏数据不能透出：宁可不画进度条，也不画一个分母错误的条。
      const dirtyCases: Array<Record<string, unknown>> = [
        { ...validUsage, used_tokens: 'x' },
        { ...validUsage, used_tokens: -1 },
        { ...validUsage, limit_tokens: 0 },
        { ...validUsage, limit_tokens: null },
        { ...validUsage, compact_trigger_tokens: undefined },
        { ...validUsage, context_window_tokens: -5 },
        { ...validUsage, ratio: Number.NaN },
        { ...validUsage, ratio: -0.1 },
      ];

      for (const context_usage of dirtyCases) {
        expect(
          normalizeWsEventPayload({ event: 'agent_context_usage', team_id: 2, agent_id: 7, context_usage }),
          `应当丢弃脏载荷: ${JSON.stringify(context_usage)}`,
        ).toBeNull();
      }
    });

    it('drops the event when context_usage is absent or not an object', () => {
      expect(normalizeWsEventPayload({
        event: 'agent_context_usage',
        team_id: 2,
        agent_id: 7,
      })).toBeNull();

      expect(normalizeWsEventPayload({
        event: 'agent_context_usage',
        team_id: 2,
        agent_id: 7,
        context_usage: 'nope',
      })).toBeNull();
    });
  });
});
