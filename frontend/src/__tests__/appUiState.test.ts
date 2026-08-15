import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  clearGlobalRequestError,
  globalRequestErrors,
  setGlobalRequestErrorAutoDismiss,
  showGlobalRequestError,
} from '../appUiState';

describe('appUiState request error toasts', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-04-26T00:00:00Z'));
    clearGlobalRequestError();
    setGlobalRequestErrorAutoDismiss(5000);
  });

  it('creates fresh toasts with a stable zero countdown delay', () => {
    showGlobalRequestError({
      title: 'Request Failed',
      path: '/test',
      detail: 'failure',
    });

    expect(globalRequestErrors.value).toHaveLength(1);
    expect(globalRequestErrors.value[0]?.countdownDelayMs).toBe(0);
    expect(globalRequestErrors.value[0]?.autoDismissMs).toBe(5000);
  });

  it('reschedules existing toasts with a fixed negative delay instead of recomputing on render', () => {
    showGlobalRequestError({
      title: 'Request Failed',
      path: '/test',
      detail: 'failure',
    });

    vi.advanceTimersByTime(1200);
    setGlobalRequestErrorAutoDismiss(5000);

    expect(globalRequestErrors.value).toHaveLength(1);
    expect(globalRequestErrors.value[0]?.countdownDelayMs).toBe(-1200);
    expect(globalRequestErrors.value[0]?.dismissAt).toBe(Date.now() + 5000);
  });
});
