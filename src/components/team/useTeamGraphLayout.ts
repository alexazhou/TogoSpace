import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue';

type UseTeamGraphLayoutOptions = {
  readonly: Ref<boolean>;
  selectedAgents: Ref<string[]>;
  contentVersion?: Ref<string>;
};

export function useTeamGraphLayout(options: UseTeamGraphLayoutOptions) {
  const graphRef = ref<HTMLElement | null>(null);
  const canvasRef = ref<HTMLElement | null>(null);
  const memberTreeRef = ref<HTMLElement | null>(null);
  const graphWidth = ref(0);
  const graphHeight = ref(0);
  const dragContentMinLeft = ref(0);
  const dragContentMaxRight = ref(0);
  const dragContentMinTop = ref(0);
  const dragContentMaxBottom = ref(0);
  const panX = ref(0);
  const panY = ref(0);
  const zoom = ref(1);
  const isPanning = ref(false);
  const railStartX = ref(0);
  const railEndX = ref(0);

  const canvasStyle = computed(() => ({
    transform: `translate(-50%, 0) translate(${panX.value}px, ${panY.value}px) scale(${zoom.value})`,
  }));
  const railStyle = computed(() => ({
    left: `${railStartX.value}px`,
    right: `${railEndX.value}px`,
  }));

  let resizeObserver: ResizeObserver | null = null;
  let panStartX = 0;
  let panStartY = 0;
  let panOriginX = 0;
  let panOriginY = 0;
  let metricsFrame = 0;

  function updateMetrics(): void {
    graphWidth.value = graphRef.value?.clientWidth ?? 0;
    graphHeight.value = graphRef.value?.clientHeight ?? 0;

    const canvas = canvasRef.value;
    const graph = graphRef.value;
    if (!canvas || !graph) {
      return;
    }

    const nodes = Array.from(canvas.querySelectorAll<HTMLElement>(
      '.team-root, .member-node, .member-action-button, .member-rail, .member-single-link, .member-top-link, .member-child-rail, .member-child-link',
    ));
    if (nodes.length === 0) {
      return;
    }

    const graphRect = graph.getBoundingClientRect();
    const treeRect = memberTreeRef.value?.getBoundingClientRect() ?? null;
    let graphMinLeft = Number.POSITIVE_INFINITY;
    let graphMaxRight = Number.NEGATIVE_INFINITY;
    let graphMinTop = Number.POSITIVE_INFINITY;
    let graphMaxBottom = Number.NEGATIVE_INFINITY;
    const memberCenters: number[] = [];

    for (const node of nodes) {
      const rect = node.getBoundingClientRect();
      graphMinLeft = Math.min(graphMinLeft, rect.left - graphRect.left);
      graphMaxRight = Math.max(graphMaxRight, rect.right - graphRect.left);
      graphMinTop = Math.min(graphMinTop, rect.top - graphRect.top);
      graphMaxBottom = Math.max(graphMaxBottom, rect.bottom - graphRect.top);
    }

    if (treeRect) {
      const memberNodes = Array.from(canvas.querySelectorAll<HTMLElement>('.top-level-node'));
      for (const node of memberNodes) {
        const rect = node.getBoundingClientRect();
        memberCenters.push((rect.left + rect.right) / 2 - treeRect.left);
      }
    }

    dragContentMinLeft.value = graphMinLeft - panX.value;
    dragContentMaxRight.value = graphMaxRight - panX.value;
    dragContentMinTop.value = graphMinTop - panY.value;
    dragContentMaxBottom.value = graphMaxBottom - panY.value;

    if (memberCenters.length > 0 && treeRect) {
      const scale = zoom.value || 1;
      railStartX.value = Math.min(...memberCenters) / scale;
      railEndX.value = Math.max((treeRect.width - Math.max(...memberCenters)) / scale, 0);
      return;
    }

    railStartX.value = 0;
    railEndX.value = 0;
  }

  function scheduleMetricsUpdate(): void {
    if (metricsFrame) {
      cancelAnimationFrame(metricsFrame);
    }
    metricsFrame = requestAnimationFrame(() => {
      metricsFrame = 0;
      updateMetrics();
    });
  }

  function clamp(value: number, min: number, max: number): number {
    return Math.min(max, Math.max(min, value));
  }

  function clampPan(nextX: number, nextY: number): { x: number; y: number } {
    const keepVisiblePx = 10;
    const minX = keepVisiblePx - dragContentMaxRight.value;
    const maxX = graphWidth.value - keepVisiblePx - dragContentMinLeft.value;
    const minY = keepVisiblePx - dragContentMaxBottom.value;
    const maxY = graphHeight.value - keepVisiblePx - dragContentMinTop.value;

    return {
      x: clamp(nextX, Math.min(minX, maxX), Math.max(minX, maxX)),
      y: clamp(nextY, Math.min(minY, maxY), Math.max(minY, maxY)),
    };
  }

  function resetPan(): void {
    const next = clampPan(
      (graphWidth.value - dragContentMaxRight.value - dragContentMinLeft.value) / 2,
      (graphHeight.value - dragContentMaxBottom.value - dragContentMinTop.value) / 2,
    );
    panX.value = next.x;
    panY.value = next.y;
    scheduleMetricsUpdate();
  }

  function startPan(event: PointerEvent): void {
    if (event.button !== 0 || !graphRef.value) {
      return;
    }

    const target = event.target instanceof HTMLElement ? event.target : null;
    if (!options.readonly.value && target?.closest('button')) {
      return;
    }

    graphRef.value.focus({ preventScroll: true });
    isPanning.value = true;
    panStartX = event.clientX;
    panStartY = event.clientY;
    panOriginX = panX.value;
    panOriginY = panY.value;
    graphRef.value.setPointerCapture(event.pointerId);
  }

  function movePan(event: PointerEvent): void {
    if (!isPanning.value) {
      return;
    }

    const next = clampPan(
      panOriginX + event.clientX - panStartX,
      panOriginY + event.clientY - panStartY,
    );
    panX.value = next.x;
    panY.value = next.y;
    scheduleMetricsUpdate();
  }

  function endPan(event?: PointerEvent): void {
    if (!isPanning.value) {
      return;
    }

    if (event && graphRef.value?.hasPointerCapture(event.pointerId)) {
      graphRef.value.releasePointerCapture(event.pointerId);
    }

    isPanning.value = false;
  }

  function handleWheelZoom(event: WheelEvent): void {
    if (!graphRef.value || event.deltaY === 0) {
      return;
    }

    const activeElement = document.activeElement;
    if (activeElement !== graphRef.value && !graphRef.value.contains(activeElement)) {
      return;
    }

    event.preventDefault();
    const minZoom = 0.6;
    const maxZoom = 1.8;
    const zoomFactor = Math.exp(-event.deltaY * 0.0015);
    const nextZoom = clamp(zoom.value * zoomFactor, minZoom, maxZoom);

    if (Math.abs(nextZoom - zoom.value) < 0.0001) {
      return;
    }

    zoom.value = nextZoom;
  }

  onMounted(() => {
    updateMetrics();
    resetPan();
    if (!graphRef.value || !canvasRef.value) {
      return;
    }

    resizeObserver = new ResizeObserver(() => {
      updateMetrics();
      resetPan();
    });
    resizeObserver.observe(graphRef.value);
    resizeObserver.observe(canvasRef.value);
  });

  onBeforeUnmount(() => {
    resizeObserver?.disconnect();
    resizeObserver = null;
    if (metricsFrame) {
      cancelAnimationFrame(metricsFrame);
      metricsFrame = 0;
    }
  });

  watch(
    [options.selectedAgents, options.readonly, options.contentVersion ?? computed(() => '')],
    async () => {
      await nextTick();
      updateMetrics();
      resetPan();
    },
    { deep: true, immediate: true },
  );

  watch([panX, panY], async () => {
    await nextTick();
    updateMetrics();
  });

  watch(zoom, async () => {
    await nextTick();
    updateMetrics();
    const next = clampPan(panX.value, panY.value);
    panX.value = next.x;
    panY.value = next.y;
  });

  return {
    graphRef,
    canvasRef,
    memberTreeRef,
    isPanning,
    canvasStyle,
    railStyle,
    startPan,
    movePan,
    endPan,
    handleWheelZoom,
  };
}
