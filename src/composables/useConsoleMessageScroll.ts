import { nextTick, ref, type Ref } from 'vue';

export function useConsoleMessageScroll(messageViewport: Ref<HTMLElement | null | undefined>) {
  const shouldFollowMessages = ref(true);
  let boundMessageStream: HTMLElement | null = null;

  function getMessageStream(): HTMLElement | null {
    const viewport = messageViewport.value?.querySelector('.message-stream');
    return viewport instanceof HTMLElement ? viewport : null;
  }

  function isAtBottom(viewport: HTMLElement): boolean {
    const distanceToBottom = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    return distanceToBottom <= 4;
  }

  function syncFollowMessages(viewport?: HTMLElement | null): void {
    const target = viewport ?? getMessageStream();
    if (!target) {
      shouldFollowMessages.value = true;
      return;
    }
    shouldFollowMessages.value = isAtBottom(target);
  }

  function handleMessageScroll(): void {
    syncFollowMessages();
  }

  function bindMessageScrollListener(): void {
    const viewport = getMessageStream();
    if (!viewport || viewport === boundMessageStream) {
      return;
    }

    boundMessageStream?.removeEventListener('scroll', handleMessageScroll);
    viewport.addEventListener('scroll', handleMessageScroll, { passive: true });
    boundMessageStream = viewport;
    syncFollowMessages(viewport);
  }

  async function scrollMessagesToBottom(): Promise<void> {
    await nextTick();
    const viewport = getMessageStream();
    if (!viewport) {
      return;
    }

    viewport.scrollTop = viewport.scrollHeight;
    shouldFollowMessages.value = true;
  }

  function cleanupMessageScroll(): void {
    boundMessageStream?.removeEventListener('scroll', handleMessageScroll);
    boundMessageStream = null;
  }

  return {
    shouldFollowMessages,
    bindMessageScrollListener,
    scrollMessagesToBottom,
    cleanupMessageScroll,
  };
}
