import type { MessageInfo } from '../types';

/**
 * 「待读」判定纯函数 —— 聊天页与 Agent 活动面板共用。
 *
 * 抽出来的动机（设计文档 3.2）：这两处展示的是同一类数据（发给某个 AI、还没被它读到的
 * 私聊消息），如果各写一套判定，两份规则会慢慢走样。这里只有函数与常量，不依赖 Vue、
 * 不读 store、不知道房间类型从哪来——调用方把上下文准备好再传进来。
 */

/** OPERATOR 的虚拟身份 ID（与后端 `SpecialAgent.OPERATOR.value` 一致）。 */
export const OPERATOR_ID = -1;

/** SYSTEM 的虚拟身份 ID（与后端 `SpecialAgent.SYSTEM.value` 一致）。 */
export const SYSTEM_ID = -2;

/** 调用方需要提供的判定上下文。字段都是「已经查好的事实」，这里不做异步取数。 */
export interface ReadCursorContext {
  /** 是否私聊房间。群聊没有「排队 / 注入」概念，恒视为已读。 */
  isPrivate?: boolean;
  /** 私聊房间成员 ID 列表（含 Operator -1 与 AI 正数 ID），用于定位接收方 AI。 */
  roomAgents?: number[];
  /** 房间内各 Agent 的读游标：{ [agentId]: 下一条待读的 seq }。 */
  agentReadIndex?: Record<string, number>;
  /**
   * 旧的单游标入参，只用于兼容存量调用与测试。
   * 优先用 `roomAgents` + `agentReadIndex`（一条规则同时覆盖 Operator↔AI 与 AI↔AI）。
   */
  agentReadSeq?: number;
}

/**
 * 解析「这条私聊消息的接收方 AI 的读游标」。
 *
 * 返回 null 表示「不存在接收方 AI 读游标」，即恒视为已读：
 * - 非私聊（群聊没有排队 / 注入概念）；
 * - SYSTEM 消息；
 * - 单聊里找不到非发送者的 AI（典型是 Operator + AI 单聊中 AI 发给 Operator 的消息）。
 */
export function resolveReceiverReadSeq(
  message: MessageInfo,
  context: ReadCursorContext,
): number | null {
  if (!context.isPrivate || message.sender_id === SYSTEM_ID) {
    return null;
  }
  const roomAgents = context.roomAgents;
  if (roomAgents && roomAgents.length > 0) {
    const receiverId = roomAgents.find((id) => id > 0 && id !== message.sender_id);
    if (receiverId === undefined) {
      return null;
    }
    const seq = context.agentReadIndex?.[String(receiverId)];
    return typeof seq === 'number' && Number.isFinite(seq) ? seq : 0;
  }
  // 兼容仅传入 agentReadSeq 的存量调用 / 测试
  if (message.sender_id === OPERATOR_ID) {
    return context.agentReadSeq ?? 0;
  }
  return null;
}

/**
 * 是否为「私聊中发出、接收方 AI 尚未读到」的消息。
 *
 * 两条缺一不可：
 * - `resolveReceiverReadSeq` 非 null：存在接收方 AI 读游标；
 * - `seq >= readSeq`：读游标语义是「下一条待读的 seq」，等于游标的也还没读。
 */
export function isUnreadPrivateMessage(
  message: MessageInfo,
  context: ReadCursorContext,
): boolean {
  const readSeq = resolveReceiverReadSeq(message, context);
  if (readSeq === null) {
    return false;
  }
  return message.seq >= readSeq;
}

/** 待读条目在浮层 / 待读区里的两种状态。与 `PendingStatus` 同源，此处先行定义避免环依赖。 */
export type PendingStatusValue = 'pending-immediate' | 'queued';

/**
 * 待读状态；不是待读消息时返回 null。
 *
 * 只有「加急」与「排队」两种：加急的被 Agent 在下一个安全边界注入，排队的等本轮结束。
 * 已读消息的状态（`immediate` / `published`）属于消息流气泡的语义，不在这里表达。
 */
export function resolvePendingStatus(
  message: MessageInfo,
  context: ReadCursorContext,
): PendingStatusValue | null {
  if (!isUnreadPrivateMessage(message, context)) {
    return null;
  }
  return message.insert_immediately ? 'pending-immediate' : 'queued';
}

/**
 * 消息流气泡的状态。保留在共用层是为了让聊天页与待读组件对「同一条消息」的判定
 * 只有一个来源，避免两处对 `insert_immediately` 的解释分叉。
 */
export type MessageStatusValue = 'published' | 'immediate' | 'pending-immediate' | 'queued';

export function resolveMessageStatus(
  message: MessageInfo,
  context: ReadCursorContext,
): MessageStatusValue {
  if (!context.isPrivate || message.sender_id === SYSTEM_ID) {
    return 'published';
  }
  if (isUnreadPrivateMessage(message, context)) {
    return message.insert_immediately ? 'pending-immediate' : 'queued';
  }
  return message.insert_immediately ? 'immediate' : 'published';
}
