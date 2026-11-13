import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue';
import { createResizeObserver } from './useResizeObserver';
import { parseFiniteNumberInRange, useLocalStorageState } from './useLocalStorageState';

const COMPOSER_DRAG_ZONE_HEIGHT_PX = 8;
const COMPOSER_RATIO_STORAGE_KEY = 'chat-composer-height-ratio';

type Metrics = {
  availableHeight: number;
  minComposerHeight: number;
  maxComposerHeight: number;
  composerHeight: number;
};

/**
 * 聊天输入框高度拖拽（从 `ChatPanel.vue` 抽出，P4）。
 *
 * 逻辑逐行搬移，未改判据与执行顺序：高度比例持久化到 localStorage、拖拽期间锁定 body
 * 光标/选区、拖动时保持消息流「距底距离」不跳、四个目标元素任一尺寸变化都重算。
 *
 * 输入输出约定：
 * - 入参是四个模板 ref + 三个展示条件（是否有输入框 / 是否有 banner / 占位提示文案）；
 * - 出参给模板用 `chatLayoutStyle`（整格行高）与 `composerStyle`（输入框高度），
 *   `dragging` 供拖拽把手高亮，`startResize` 绑定 `pointerdown`。
 */
export function useChatComposerResize(options: {
  chatRef: Readonly<Ref<HTMLElement | null>>;
  chatHeadRef: Readonly<Ref<HTMLElement | null>>;
  chatBannerRef: Readonly<Ref<HTMLElement | null>>;
  messageViewportRef: Readonly<Ref<HTMLElement | null>>;
  hasComposer: Readonly<Ref<boolean>>;
  hasBanner: Readonly<Ref<boolean>>;
  composerNotice: () => string;
}) {
  const { chatRef, chatHeadRef, chatBannerRef, messageViewportRef, hasComposer, hasBanner, composerNotice } = options;

  const composerDividerDragging = ref(false);
  const composerHeightRatio = ref(0.28);

  const chatHeight = ref(0);
  const chatHeadHeight = ref(0);
  const chatBannerHeight = ref(0);

  const composerRatioStorage = useLocalStorageState<number>(COMPOSER_RATIO_STORAGE_KEY, {
    parse: parseFiniteNumberInRange(0.12, 0.72),
  });

  let stopComposerResize: (() => void) | null = null;
  let pendingComposerBottomAnchorSync = false;
  let pendingComposerBottomAnchorDistance: number | null = null;

  // 四个目标都是模板 ref，且部分在 `v-if` / `v-show` 之后才出现，所以按需重挂而非跟随生命周期。
  const layoutObserver = createResizeObserver(() => {
    refreshLayoutMetrics();
    if (composerDividerDragging.value && pendingComposerBottomAnchorDistance !== null) {
      preserveMessageBottomAnchor(pendingComposerBottomAnchorDistance);
    }
  });

  const composerMetrics = computed<Metrics | null>(() => {
    if (!hasComposer.value) {
      return null;
    }

    const reservedHeight = chatHeadHeight.value
      + (hasBanner.value ? chatBannerHeight.value : 0);
    const availableHeight = chatHeight.value - reservedHeight;
    if (availableHeight <= 0) {
      return null;
    }

    const minComposerHeight = Math.min(220, Math.max(100, Math.round(availableHeight * 0.18)));
    const minMessageHeight = Math.min(260, Math.max(120, Math.round(availableHeight * 0.24)));
    const maxComposerHeight = Math.max(minComposerHeight, availableHeight - minMessageHeight);
    const composerHeight = Math.round(
      Math.min(
        maxComposerHeight,
        Math.max(minComposerHeight, availableHeight * composerHeightRatio.value),
      ),
    );

    return {
      availableHeight,
      minComposerHeight,
      maxComposerHeight,
      composerHeight,
    };
  });

  const chatLayoutStyle = computed(() => {
    if (hasComposer.value) {
      const composerHeight = composerMetrics.value?.composerHeight ?? 200;
      return {
        gridTemplateRows: hasBanner.value
          ? `auto auto minmax(0, 1fr) ${composerHeight}px`
          : `auto minmax(0, 1fr) ${composerHeight}px`,
      };
    }

    if (composerNotice()) {
      return {
        gridTemplateRows: hasBanner.value
          ? 'auto auto minmax(0, 1fr) auto'
          : 'auto minmax(0, 1fr) auto',
      };
    }

    return {
      gridTemplateRows: hasBanner.value
        ? 'auto auto minmax(0, 1fr)'
        : 'auto minmax(0, 1fr)',
    };
  });

  const composerStyle = computed(() => (
    hasComposer.value
      ? {
        height: `${composerMetrics.value?.composerHeight ?? 200}px`,
        '--composer-drag-zone-height': `${COMPOSER_DRAG_ZONE_HEIGHT_PX}px`,
      }
      : {}
  ));

  function persistComposerHeightRatio(): void {
    composerRatioStorage.write(composerHeightRatio.value);
  }

  function restoreComposerHeightRatio(): void {
    const stored = composerRatioStorage.read();
    if (stored !== null) {
      composerHeightRatio.value = stored;
    }
  }

  function refreshLayoutMetrics(): void {
    chatHeight.value = chatRef.value?.clientHeight ?? 0;
    chatHeadHeight.value = chatHeadRef.value?.clientHeight ?? 0;
    chatBannerHeight.value = chatBannerRef.value?.clientHeight ?? 0;
  }

  function bindLayoutResizeObserver(): void {
    layoutObserver.disconnect();
    // 四个目标任意一个变高变矮都会影响输入框可用高度；未绑定的 ref 由工厂忽略。
    layoutObserver.observe(chatRef.value);
    layoutObserver.observe(chatHeadRef.value);
    layoutObserver.observe(chatBannerRef.value);
    layoutObserver.observe(messageViewportRef.value);
  }

  function resetComposerDragState(): void {
    composerDividerDragging.value = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  }

  function getMessageStreamElement(): HTMLElement | null {
    const stream = chatRef.value?.querySelector('.message-stream');
    return stream instanceof HTMLElement ? stream : null;
  }

  function preserveMessageBottomAnchor(distanceToBottom: number): void {
    const stream = getMessageStreamElement();
    if (!stream) {
      return;
    }

    const nextScrollTop = stream.scrollHeight - stream.clientHeight - distanceToBottom;
    stream.scrollTop = Math.max(0, nextScrollTop);
  }

  function scheduleComposerBottomAnchorSync(): void {
    if (pendingComposerBottomAnchorSync) {
      return;
    }

    pendingComposerBottomAnchorSync = true;
    void nextTick(() => {
      pendingComposerBottomAnchorSync = false;
      if (pendingComposerBottomAnchorDistance !== null) {
        preserveMessageBottomAnchor(pendingComposerBottomAnchorDistance);
      }
    });
  }

  function startResize(event: PointerEvent): void {
    const metrics = composerMetrics.value;
    if (!metrics) {
      return;
    }

    event.preventDefault();

    const startHeight = metrics.composerHeight;
    const startY = event.clientY;

    composerDividerDragging.value = true;
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';

    const stopResize = (): void => {
      resetComposerDragState();
      pendingComposerBottomAnchorDistance = null;
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', stopResize);
      window.removeEventListener('pointercancel', stopResize);
      stopComposerResize = null;
    };

    const handlePointerMove = (moveEvent: PointerEvent): void => {
      const stream = getMessageStreamElement();
      const distanceToBottom = stream
        ? stream.scrollHeight - stream.scrollTop - stream.clientHeight
        : 0;
      const nextComposerHeight = Math.min(
        metrics.maxComposerHeight,
        Math.max(metrics.minComposerHeight, startHeight - (moveEvent.clientY - startY)),
      );
      pendingComposerBottomAnchorDistance = distanceToBottom;
      composerHeightRatio.value = nextComposerHeight / metrics.availableHeight;
      persistComposerHeightRatio();
      scheduleComposerBottomAnchorSync();
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', stopResize, { once: true });
    window.addEventListener('pointercancel', stopResize, { once: true });
    stopComposerResize = stopResize;
  }

  watch(
    () => [hasBanner.value, hasComposer.value, composerNotice()] as const,
    async () => {
      await nextTick();
      refreshLayoutMetrics();
      bindLayoutResizeObserver();
    },
  );

  onMounted(async () => {
    restoreComposerHeightRatio();
    await nextTick();
    refreshLayoutMetrics();
    bindLayoutResizeObserver();
  });

  onBeforeUnmount(() => {
    stopComposerResize?.();
    stopComposerResize = null;
    pendingComposerBottomAnchorDistance = null;
    pendingComposerBottomAnchorSync = false;
    layoutObserver.disconnect();
    resetComposerDragState();
  });

  return {
    composerDividerDragging,
    chatLayoutStyle,
    composerStyle,
    refreshLayoutMetrics,
    bindLayoutResizeObserver,
    startResize,
  };
}
