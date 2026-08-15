import { ref } from 'vue';
import type { ConnectionState } from './utils';

export interface GlobalToastItem {
  id: number;
  message: string;
}

export interface GlobalRequestErrorToast {
  id: number;
  title: string;
  path: string;
  statusCode: number | null;
  detail: string;
  autoDismissMs: number | null;
  dismissAt: number | null;
  countdownDelayMs: number;
}

export interface GlobalRequestErrorPayload {
  title: string;
  path: string;
  statusCode?: number | null;
  detail?: string;
}

export const connectionState = ref<ConnectionState>('connected');
export const reconnectProgress = ref(0);
export const totalMessageCount = ref(0);
export const globalRequestErrors = ref<GlobalRequestErrorToast[]>([]);
export const globalSuccessToasts = ref<GlobalToastItem[]>([]);
export const showQuickInit = ref(false);
export const showTokenDialog = ref(false);
export const authEnabled = ref(false);
export const scheduleState = ref<'stopped' | 'blocked' | 'running' | ''>('');
export const scheduleNotRunningReason = ref('');
export const appVersion = ref('');
export const autoCheckUpdate = ref(true);
export const latestVersion = ref('');
export const hasUpdate = ref(false);
export const releaseUrl = ref('');

type ScheduleStateInput = 'stopped' | 'blocked' | 'running' | 'STOPPED' | 'BLOCKED' | 'RUNNING' | '';

let nextGlobalToastId = 1;
let globalRequestErrorAutoDismissMs: number | null = 5000;
const globalRequestErrorTimers = new Map<number, number>();
const globalSuccessToastTimers = new Map<number, number>();

function patchGlobalRequestError(
  toastId: number,
  patch: Partial<Pick<GlobalRequestErrorToast, 'autoDismissMs' | 'dismissAt' | 'countdownDelayMs'>>,
): void {
  globalRequestErrors.value = globalRequestErrors.value.map((toast) => (
    toast.id === toastId ? { ...toast, ...patch } : toast
  ));
}

function removeGlobalRequestError(toastId: number): void {
  globalRequestErrors.value = globalRequestErrors.value.filter((toast) => toast.id !== toastId);
  const timer = globalRequestErrorTimers.get(toastId);
  if (timer !== undefined) {
    window.clearTimeout(timer);
    globalRequestErrorTimers.delete(toastId);
  }
}

function scheduleGlobalRequestErrorRemoval(toastId: number): void {
  const currentToast = globalRequestErrors.value.find((toast) => toast.id === toastId) ?? null;
  const currentTimer = globalRequestErrorTimers.get(toastId);
  if (currentTimer !== undefined) {
    window.clearTimeout(currentTimer);
    globalRequestErrorTimers.delete(toastId);
  }

  if (globalRequestErrorAutoDismissMs === null) {
    patchGlobalRequestError(toastId, {
      autoDismissMs: null,
      dismissAt: null,
      countdownDelayMs: 0,
    });
    return;
  }

  const previousDismissAt = currentToast?.dismissAt ?? null;
  const previousAutoDismissMs = currentToast?.autoDismissMs ?? null;
  const previousStartAt = (
    previousDismissAt !== null && previousAutoDismissMs !== null
      ? previousDismissAt - previousAutoDismissMs
      : null
  );
  const elapsedMs = previousStartAt !== null
    ? Math.max(0, Date.now() - previousStartAt)
    : 0;
  const countdownDelayMs = elapsedMs > 0
    ? -Math.min(elapsedMs, globalRequestErrorAutoDismissMs)
    : 0;
  const dismissAt = Date.now() + globalRequestErrorAutoDismissMs;
  patchGlobalRequestError(toastId, {
    autoDismissMs: globalRequestErrorAutoDismissMs,
    dismissAt,
    countdownDelayMs,
  });
  const timer = window.setTimeout(() => {
    removeGlobalRequestError(toastId);
  }, globalRequestErrorAutoDismissMs);
  globalRequestErrorTimers.set(toastId, timer);
}

function removeGlobalSuccessToast(toastId: number): void {
  globalSuccessToasts.value = globalSuccessToasts.value.filter((toast) => toast.id !== toastId);
  const timer = globalSuccessToastTimers.get(toastId);
  if (timer !== undefined) {
    window.clearTimeout(timer);
    globalSuccessToastTimers.delete(toastId);
  }
}

export function showGlobalRequestError(message: GlobalRequestErrorPayload): void {
  const toastId = nextGlobalToastId++;
  globalRequestErrors.value = [
    ...globalRequestErrors.value,
    {
      id: toastId,
      title: message.title,
      path: message.path,
      statusCode: message.statusCode ?? null,
      detail: message.detail?.trim() ?? '',
      autoDismissMs: null,
      dismissAt: null,
      countdownDelayMs: 0,
    },
  ];
  scheduleGlobalRequestErrorRemoval(toastId);
}

export function clearGlobalRequestError(toastId?: number): void {
  if (toastId === undefined) {
    globalRequestErrors.value.forEach((toast) => removeGlobalRequestError(toast.id));
    return;
  }
  removeGlobalRequestError(toastId);
}

export function setGlobalRequestErrorAutoDismiss(durationMs: number | null): void {
  globalRequestErrorAutoDismissMs = durationMs;
  globalRequestErrors.value.forEach((toast) => scheduleGlobalRequestErrorRemoval(toast.id));
}

export function showGlobalSuccessToast(message: string, durationMs = 2400): void {
  const toastId = nextGlobalToastId++;
  globalSuccessToasts.value = [...globalSuccessToasts.value, { id: toastId, message }];
  const timer = window.setTimeout(() => {
    removeGlobalSuccessToast(toastId);
  }, durationMs);
  globalSuccessToastTimers.set(toastId, timer);
}

export function clearGlobalSuccessToast(toastId?: number): void {
  if (toastId === undefined) {
    globalSuccessToasts.value.forEach((toast) => removeGlobalSuccessToast(toast.id));
    return;
  }
  removeGlobalSuccessToast(toastId);
}

function normalizeScheduleState(state: ScheduleStateInput): 'stopped' | 'blocked' | 'running' | '' {
  const normalized = String(state).trim().toLowerCase();
  if (normalized === 'stopped' || normalized === 'blocked' || normalized === 'running') {
    return normalized;
  }
  return '';
}

export function updateScheduleState(state: ScheduleStateInput, reason?: string): void {
  scheduleState.value = normalizeScheduleState(state);
  scheduleNotRunningReason.value = reason ?? '';
}
