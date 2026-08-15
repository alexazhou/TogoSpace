import { t } from '../i18n';
import type { MessageInfo, RoomState } from '../types';
import { formatPreview } from '../utils';

export type ResolveRoomPreviewOptions = {
  messages?: MessageInfo[];
  previousRoom?: Pick<RoomState, 'preview'> | null;
  preview?: string | null;
  resolveSenderDisplayName: (senderId: number) => string;
};

export function resolveRoomPreview(options: ResolveRoomPreviewOptions): string {
  const lastMessage = options.messages?.[options.messages.length - 1];
  if (lastMessage) {
    return formatPreview(
      options.resolveSenderDisplayName(lastMessage.sender_id),
      lastMessage.content,
    );
  }

  const fallbackPreview = options.preview ?? options.previousRoom?.preview ?? '';
  return fallbackPreview || t('message.noMessage');
}
