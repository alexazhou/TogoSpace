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
});
