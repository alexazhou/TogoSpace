import type { MessageInfo } from '../../../types';

/**
 * 待读浮层（聊天页）与待读区（Agent 活动面板）共用的数据结构。
 *
 * 设计原则（设计文档 5.3）：**组件不关心读游标、房间类型这些业务判断**。
 * 调用方先算好「哪些消息待读、状态是什么、发送者怎么显示」，再把 `PendingMessageItem`
 * 交给组件；组件只负责展示与交互。这样两处只是数据来源不同，展示完全一致。
 */

/** 待读状态。加急的会在下一个安全边界注入，排队的等本轮结束。 */
export type PendingStatus = 'pending-immediate' | 'queued';

export interface PendingSender {
  displayName: string;
  avatarUrl: string;
  /** 是否可点击头像跳转 Agent。Operator / SYSTEM 不可点。 */
  clickable: boolean;
}

export interface PendingMessageItem {
  /**
   * 列表 key。用 `db_id` 优先，兜底 `time + sender_id`（DB 未落库的消息没有 db_id），
   * 同一批消息里保证唯一，且消息「转正」后 key 不变，避免展开状态乱跳。
   */
  key: string;
  message: MessageInfo;
  status: PendingStatus;
  sender: PendingSender;
}

/**
 * 构造列表 key。抽成函数而不是内联在调用方：两处接入必须用同一套规则，
 * 否则同一个消息在两处会有不同 key，展开状态就无法对齐。
 */
export function buildPendingKey(message: MessageInfo, index = 0): string {
  if (message.db_id !== null && message.db_id !== undefined) {
    return String(message.db_id);
  }
  return `${message.time}-${message.sender_id}-${index}`;
}
