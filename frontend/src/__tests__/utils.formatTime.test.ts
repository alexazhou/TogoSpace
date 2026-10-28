import { describe, expect, it } from 'vitest';
import { formatBackendDateTime, formatTime } from '../utils';

describe('formatTime', () => {
  it('renders the date together with the 24-hour clock', () => {
    expect(formatTime('2026-10-28 02:07:41.123456')).toBe('2026/10/28 02:07:41');
  });

  it('does not zero-pad month and day', () => {
    expect(formatTime('2026-01-05 09:03:00.000000')).toBe('2026/1/5 09:03:00');
  });

  it('stays 24-hour for afternoon timestamps', () => {
    // 跟随 locale 的 toLocaleTimeString 会输出 06:07:41 PM
    expect(formatTime('2026-10-28 18:07:41.000000')).toBe('2026/10/28 18:07:41');
  });

  it('returns an empty string for unparsable input', () => {
    expect(formatTime('')).toBe('');
    expect(formatTime('not-a-date')).toBe('');
  });

  it('matches formatBackendDateTime so the chat and the task timeline agree', () => {
    const raw = '2026-10-28 02:07:41.123456';
    expect(formatTime(raw)).toBe(formatBackendDateTime(raw));
  });
});
