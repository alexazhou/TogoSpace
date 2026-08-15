import { computed, onBeforeUnmount, onMounted, ref, type Ref } from 'vue';

const SPLITTER_HEIGHT_PX = 8;
const SIDEBAR_TOP_RATIO_STORAGE_KEY = 'console-left-stack-top-ratio';

export function useConsoleSidebarLayout(leftStack: Ref<HTMLElement | null | undefined>) {
  const leftStackHeight = ref(0);
  const sidebarDividerDragging = ref(false);
  const sidebarTopRatio = ref(0.62);

  let leftStackResizeObserver: ResizeObserver | null = null;

  const leftStackStyle = computed(() => {
    if (leftStackHeight.value <= SPLITTER_HEIGHT_PX) {
      return {};
    }

    const availableHeight = leftStackHeight.value - SPLITTER_HEIGHT_PX;
    const minTopHeight = Math.min(220, Math.max(120, Math.round(availableHeight * 0.28)));
    const minBottomHeight = Math.min(180, Math.max(108, Math.round(availableHeight * 0.22)));
    const maxTopHeight = Math.max(minTopHeight, availableHeight - minBottomHeight);
    const topHeight = Math.round(
      Math.min(maxTopHeight, Math.max(minTopHeight, availableHeight * sidebarTopRatio.value)),
    );

    return {
      gridTemplateRows: `${topHeight}px ${SPLITTER_HEIGHT_PX}px minmax(${minBottomHeight}px, 1fr)`,
    };
  });

  function persistSidebarTopRatio(): void {
    try {
      localStorage.setItem(SIDEBAR_TOP_RATIO_STORAGE_KEY, String(sidebarTopRatio.value));
    } catch {
      // ignore localStorage failures
    }
  }

  function restoreSidebarTopRatio(): void {
    try {
      const raw = localStorage.getItem(SIDEBAR_TOP_RATIO_STORAGE_KEY);
      if (!raw) {
        return;
      }

      const parsed = Number(raw);
      if (Number.isFinite(parsed) && parsed >= 0.2 && parsed <= 0.8) {
        sidebarTopRatio.value = parsed;
      }
    } catch {
      // ignore localStorage failures
    }
  }

  function refreshLeftStackHeight(): void {
    leftStackHeight.value = leftStack.value?.clientHeight ?? 0;
  }

  function resetDragState(): void {
    sidebarDividerDragging.value = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  }

  function startSidebarResize(event: PointerEvent): void {
    const container = leftStack.value;
    if (!container) {
      return;
    }

    event.preventDefault();

    const availableHeight = container.getBoundingClientRect().height - SPLITTER_HEIGHT_PX;
    if (availableHeight <= 0) {
      return;
    }

    const minTopHeight = Math.min(220, Math.max(120, Math.round(availableHeight * 0.28)));
    const minBottomHeight = Math.min(180, Math.max(108, Math.round(availableHeight * 0.22)));
    const maxTopHeight = Math.max(minTopHeight, availableHeight - minBottomHeight);
    const startTopHeight = Math.min(
      maxTopHeight,
      Math.max(minTopHeight, availableHeight * sidebarTopRatio.value),
    );
    const startY = event.clientY;

    sidebarDividerDragging.value = true;
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';

    const stopResize = (): void => {
      resetDragState();
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', stopResize);
    };

    const handlePointerMove = (moveEvent: PointerEvent): void => {
      const nextTopHeight = Math.min(
        maxTopHeight,
        Math.max(minTopHeight, startTopHeight + moveEvent.clientY - startY),
      );
      sidebarTopRatio.value = nextTopHeight / availableHeight;
      persistSidebarTopRatio();
    };

    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', stopResize, { once: true });
  }

  onMounted(() => {
    restoreSidebarTopRatio();
    refreshLeftStackHeight();

    if (!leftStack.value) {
      return;
    }

    leftStackResizeObserver = new ResizeObserver(() => {
      refreshLeftStackHeight();
    });
    leftStackResizeObserver.observe(leftStack.value);
  });

  onBeforeUnmount(() => {
    leftStackResizeObserver?.disconnect();
    leftStackResizeObserver = null;
    resetDragState();
  });

  return {
    leftStackStyle,
    sidebarDividerDragging,
    startSidebarResize,
  };
}
