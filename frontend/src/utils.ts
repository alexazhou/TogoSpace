import type { EntityI18n, DeptTreeNode } from './types';
import i18n, { t } from './i18n';

export type ConnectionState =
  | 'connecting'
  | 'connected'
  | 'waiting_reconnect'
  | 'reconnecting'
  | 'disconnected';
export type BubbleSide = 'left' | 'right' | 'center';

export function i18nText(i18nData: EntityI18n, field: string, fallback: string): string {
  const locale = i18n.global.locale.value;
  const textMap = i18nData[field];
  return textMap?.[locale]?.trim()
    || textMap?.['zh-CN']?.trim()
    || fallback;
}

export function displayName(entity: { name: string; i18n: EntityI18n }): string {
  return i18nText(entity.i18n, 'display_name', entity.name);
}

export function findDepartmentPath(tree: DeptTreeNode | null, agentId: number): string[] | null {
  if (!tree) {
    return null;
  }

  const name = i18nText(tree.i18n ?? {}, 'dept_name', tree.name);

  for (const child of tree.children) {
    const childPath = findDepartmentPath(child, agentId);
    if (childPath) {
      return [name, ...childPath];
    }
  }

  const isManager = tree.manager_id === agentId;
  const isMember = tree.agent_ids?.includes(agentId);
  return isManager || isMember ? [name] : null;
}

export function isDepartmentLeader(tree: DeptTreeNode | null, agentId: number): boolean {
  if (!tree) {
    return false;
  }
  if (tree.manager_id === agentId) {
    return true;
  }
  return tree.children?.some((child) => isDepartmentLeader(child, agentId)) ?? false;
}

export function formatPreview(senderDisplayName: string, content: string): string {
  return `${senderDisplayName}: ${content.replace(/\n/g, ' ')}`;
}

export function formatTime(time: string): string {
  const date = new Date(time);

  if (Number.isNaN(date.getTime())) {
    return '';
  }

  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date);
}

/**
 * 解析后端 datetime 字符串。
 *
 * 后端 `jsonUtil` 的格式是 `%Y-%m-%d %H:%M:%S.%f`（空格分隔、6 位微秒、本地时区），
 * 直接交给 `new Date()` 时 Safari / 部分 WebView 会得到 Invalid Date。
 * 这里统一归一化为 ISO 形式并裁掉多余的微秒位。解析失败返回 null。
 */
export function parseBackendDateTime(value: string | null | undefined): Date | null {
  if (!value) {
    return null;
  }
  const normalized = value.trim().replace(' ', 'T').replace(/(\.\d{3})\d+$/, '$1');
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * 后端 datetime → 「YYYY/M/D HH:mm:ss」本地时间（**24 小时制**）；解析失败返回空串。
 *
 * 不用 `toLocaleTimeString()` 的默认值：它会跟随 locale 输出 AM/PM，
 * 而这里的时间线 / 任务详情一律用 24 小时制。
 */
export function formatBackendDateTime(value: string | null | undefined): string {
  const date = parseBackendDateTime(value);
  if (!date) {
    return '';
  }
  const pad = (n: number) => String(n).padStart(2, '0');
  const day = `${date.getFullYear()}/${date.getMonth() + 1}/${date.getDate()}`;
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  return `${day} ${time}`;
}

export function bubbleSide(senderId: number): BubbleSide {
  if (senderId === -2) {
    return 'center';
  }
  if (senderId === -1) {
    return 'right';
  }
  return 'left';
}

export function formatConnectionState(state: ConnectionState): string {
  if (state === 'connected') {
    return t('connection.connected');
  }
  if (state === 'waiting_reconnect') {
    return t('connection.waitReconnect');
  }
  if (state === 'reconnecting') {
    return t('connection.reconnecting');
  }
  if (state === 'disconnected') {
    return t('connection.disconnected');
  }
  return t('connection.connecting');
}

export function stripAnsi(text: string): string {
  if (typeof text !== 'string') return text;
  // eslint-disable-next-line no-control-regex
  return text.replace(/\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g, '');
}
