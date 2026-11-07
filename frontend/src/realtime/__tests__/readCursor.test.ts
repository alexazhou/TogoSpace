import { describe, expect, it } from 'vitest';
import {
  OPERATOR_ID,
  SYSTEM_ID,
  isUnreadPrivateMessage,
  resolveMessageStatus,
  resolvePendingStatus,
  resolveReceiverReadSeq,
} from '../readCursor';
import type { MessageInfo } from '../../types';

function createMessage(overrides: Partial<MessageInfo> = {}): MessageInfo {
  return {
    db_id: 1,
    sender_id: OPERATOR_ID,
    sender_display_name: 'OPERATOR',
    content: 'hello',
    time: '2026-05-08T00:00:00Z',
    seq: 5,
    insert_immediately: false,
    ...overrides,
  };
}

describe('resolveReceiverReadSeq', () => {
  it('非私聊恒为 null（群聊没有排队/注入概念）', () => {
    expect(resolveReceiverReadSeq(createMessage(), { isPrivate: false, agentReadSeq: 5 })).toBeNull();
  });

  it('SYSTEM 消息恒为 null', () => {
    expect(resolveReceiverReadSeq(
      createMessage({ sender_id: SYSTEM_ID }),
      { isPrivate: true, roomAgents: [-1, 7], agentReadIndex: { '7': 5 } },
    )).toBeNull();
  });

  it('用 roomAgents 定位接收方 AI，读游标缺失时按 0 处理', () => {
    expect(resolveReceiverReadSeq(
      createMessage({ sender_id: OPERATOR_ID }),
      { isPrivate: true, roomAgents: [-1, 7] },
    )).toBe(0);
  });

  it('读游标非法值按 0 处理，不会返回 NaN', () => {
    expect(resolveReceiverReadSeq(
      createMessage(),
      { isPrivate: true, roomAgents: [-1, 7], agentReadIndex: { '7': Number.NaN } },
    )).toBe(0);
  });

  it('AI→Operator 方向没有 AI 接收方，恒为 null', () => {
    expect(resolveReceiverReadSeq(
      createMessage({ sender_id: 7 }),
      { isPrivate: true, roomAgents: [-1, 7], agentReadIndex: { '7': 0 } },
    )).toBeNull();
  });

  it('只传 agentReadSeq 时回退到旧语义（仅 Operator 发的消息）', () => {
    expect(resolveReceiverReadSeq(createMessage({ sender_id: OPERATOR_ID }), {
      isPrivate: true,
      agentReadSeq: 3,
    })).toBe(3);
    expect(resolveReceiverReadSeq(createMessage({ sender_id: 7 }), {
      isPrivate: true,
      agentReadSeq: 3,
    })).toBeNull();
  });
});

describe('isUnreadPrivateMessage', () => {
  const ctx = { isPrivate: true, roomAgents: [-1, 7], agentReadIndex: { '7': 5 } };

  it('seq 等于游标算未读（游标语义是「下一条待读的 seq」）', () => {
    expect(isUnreadPrivateMessage(createMessage({ seq: 5 }), ctx)).toBe(true);
  });

  it('seq 大于游标算未读', () => {
    expect(isUnreadPrivateMessage(createMessage({ seq: 6 }), ctx)).toBe(true);
  });

  it('seq 小于游标算已读', () => {
    expect(isUnreadPrivateMessage(createMessage({ seq: 4 }), ctx)).toBe(false);
  });
});

describe('resolvePendingStatus', () => {
  const ctx = { isPrivate: true, roomAgents: [-1, 7], agentReadIndex: { '7': 5 } };

  it('未读 + 加急 → pending-immediate', () => {
    expect(resolvePendingStatus(createMessage({ seq: 5, insert_immediately: true }), ctx))
      .toBe('pending-immediate');
  });

  it('未读 + 非加急 → queued', () => {
    expect(resolvePendingStatus(createMessage({ seq: 5, insert_immediately: false }), ctx))
      .toBe('queued');
  });

  it('已读 → null（不属于待读区）', () => {
    expect(resolvePendingStatus(createMessage({ seq: 4 }), ctx)).toBeNull();
  });
});

describe('resolveMessageStatus', () => {
  const ctx = { isPrivate: true, roomAgents: [-1, 7], agentReadIndex: { '7': 5 } };

  it('待读 + 加急 → pending-immediate；待读 + 排队 → queued', () => {
    expect(resolveMessageStatus(createMessage({ seq: 5, insert_immediately: true }), ctx))
      .toBe('pending-immediate');
    expect(resolveMessageStatus(createMessage({ seq: 5, insert_immediately: false }), ctx))
      .toBe('queued');
  });

  it('已读 + 加急 → immediate；已读 + 排队 → published', () => {
    expect(resolveMessageStatus(createMessage({ seq: 4, insert_immediately: true }), ctx))
      .toBe('immediate');
    expect(resolveMessageStatus(createMessage({ seq: 4, insert_immediately: false }), ctx))
      .toBe('published');
  });

  it('群聊 / SYSTEM 一律 published', () => {
    expect(resolveMessageStatus(createMessage({ insert_immediately: true }), { isPrivate: false }))
      .toBe('published');
    expect(resolveMessageStatus(
      createMessage({ sender_id: SYSTEM_ID, insert_immediately: true }),
      ctx,
    )).toBe('published');
  });
});
