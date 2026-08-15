import { describe, expect, it } from 'vitest';
import { t } from '../../i18n';
import { resolveRoomPreview } from '../roomPreview';

describe('resolveRoomPreview', () => {
  it('builds preview from the latest cached message', () => {
    const preview = resolveRoomPreview({
      messages: [
        { db_id: 1, sender_id: 1, sender_display_name: 'Alice', content: 'hello world', time: '2026-04-26 00:00:00', seq: 1, insert_immediately: false },
      ],
      resolveSenderDisplayName: () => 'Alice',
    });

    expect(preview).toContain('Alice');
    expect(preview).toContain('hello world');
  });

  it('falls back to previous preview when no cached messages exist', () => {
    const preview = resolveRoomPreview({
      previousRoom: { preview: 'cached preview' },
      resolveSenderDisplayName: () => 'Alice',
    });

    expect(preview).toBe('cached preview');
  });

  it('returns empty-state copy when no data is available', () => {
    const preview = resolveRoomPreview({
      resolveSenderDisplayName: () => 'Alice',
    });

    expect(preview).toBe(t('message.noMessage'));
  });
});
