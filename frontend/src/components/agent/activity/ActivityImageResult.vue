<template>
  <figure ref="rootRef" class="agent-activity-image" data-testid="activity-image">
    <button
      type="button"
      class="agent-activity-image__frame"
      :class="{ 'agent-activity-image__frame--error': status === 'error' }"
      :data-state="status"
      :title="t('agent.image.open')"
      :aria-label="t('agent.image.open')"
      @click="openLightbox"
    >
      <img
        v-if="shouldLoad && status !== 'error'"
        :src="image.src"
        :alt="altText"
        class="agent-activity-image__img"
        data-testid="activity-image-img"
        @load="onLoad"
        @error="onError"
      >
      <span v-if="!shouldLoad" class="agent-activity-image__hint">{{ t('agent.image.deferred') }}</span>
      <span v-else-if="status === 'loading'" class="agent-activity-image__hint">{{ t('agent.image.loading') }}</span>
      <span v-else-if="status === 'error'" class="agent-activity-image__error">{{ t('agent.image.loadFailed') }}</span>
    </button>
    <figcaption class="agent-activity-image__meta">{{ metaText }}</figcaption>
  </figure>
  <Teleport to="body">
    <div
      v-if="lightboxOpen"
      class="agent-activity-image__lightbox"
      role="dialog"
      aria-modal="true"
      :aria-label="t('agent.image.open')"
      data-testid="activity-image-lightbox"
      @click.self="closeLightbox"
    >
      <img :src="image.src" :alt="altText" class="agent-activity-image__lightbox-img">
      <button type="button" class="agent-activity-image__lightbox-close" @click="closeLightbox">
        {{ t('agent.image.close') }}
      </button>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
/**
 * read_image 活动的图片块（#361）。
 *
 * 三条硬要求（对应任务单交付标准）：
 * 1. **尺寸有上限**：图片与容器都受 `max-width: 100%` + `max-height: 200px` 约束，
 *    永远不会撑破卡片；比例由 `object-fit: contain` 保住，不裁不拉伸。
 * 2. **懒加载**：用 `IntersectionObserver`（`rootMargin: 200px` 提前一屏预取）决定
 *    何时把 `<img>` 挂进 DOM。没进可视区时只有一个占位框，几十条活动同时渲染也不会
 *    一口气解码几十张图。**浏览器没有 `IntersectionObserver` 时直接加载**——这是渲染层
 *    优化，不是功能开关，缺能力就退化成「立即加载」而不是「永远不加载」。
 * 3. **两态可见**：未进可视区 / 加载中 / 加载失败各有文案，失败时不留白也不抛错。
 *
 * 点开大图用 `<Teleport to="body">` 的全屏遮罩：卡片在滚动容器里、且有
 * `overflow` 与层叠上下文，放大图只能挂到 body 才不会被困住。
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import {
  buildImageMetaText,
  type ActivityImage,
} from './activityViewModel';

const props = defineProps<{
  image: ActivityImage;
}>();

const { t } = useI18n();

type LoadStatus = 'idle' | 'loading' | 'loaded' | 'error';

const rootRef = ref<HTMLElement | null>(null);
const shouldLoad = ref(false);
const status = ref<LoadStatus>('idle');
const lightboxOpen = ref(false);
let observer: IntersectionObserver | null = null;

const metaText = computed(() => buildImageMetaText(props.image));
const altText = computed(() => props.image.filePath || t('agent.image.alt'));

function beginLoad(): void {
  if (shouldLoad.value) {
    return;
  }
  shouldLoad.value = true;
  status.value = 'loading';
}

function stopObserving(): void {
  if (observer !== null) {
    observer.disconnect();
    observer = null;
  }
}

function startObserving(): void {
  stopObserving();
  const el = rootRef.value;
  if (el === null) {
    return;
  }
  if (typeof IntersectionObserver === 'undefined') {
    beginLoad();
    return;
  }
  observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        beginLoad();
        stopObserving();
      }
    },
    { rootMargin: '200px 0px', threshold: 0.01 },
  );
  observer.observe(el);
}

// 只在挂载时建一次观察器：`rootRef` 指向的 `<figure>` 是无条件渲染的固定根节点，
// 不会换实例。若这里再挂一个 `watch(rootRef)`，同一个元素会被观察两次、建出两个
// 观察器（实测：`lastObserver()` 拿到后建的那个，`disconnect` 断言就假红）。
onMounted(startObserving);

// 同一个组件实例被复用到另一张图（活动列表按 id 复用节点）时重新走一遍加载流程。
watch(
  () => props.image.src,
  () => {
    if (!shouldLoad.value) {
      return;
    }
    status.value = 'loading';
  },
);

function onLoad(): void {
  status.value = 'loaded';
}

/**
 * 图片加载失败：`file_path` 可能已被删除、base64 可能不完整。
 * 这里只落到 `error` 态（占位 + 文案），**不隐藏图片块**——要让用户看见「这里本该有图」。
 */
function onError(): void {
  status.value = 'error';
}

function openLightbox(): void {
  // 加载失败的图点开也是同一个占位，没有意义；只有成功加载的图才给看大图出口。
  if (status.value !== 'loaded') {
    return;
  }
  lightboxOpen.value = true;
}

function closeLightbox(): void {
  lightboxOpen.value = false;
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    closeLightbox();
  }
}

watch(lightboxOpen, (open) => {
  if (typeof window === 'undefined') {
    return;
  }
  if (open) {
    window.addEventListener('keydown', onKeydown);
  } else {
    window.removeEventListener('keydown', onKeydown);
  }
});

onUnmounted(() => {
  stopObserving();
  if (typeof window !== 'undefined') {
    window.removeEventListener('keydown', onKeydown);
  }
});
</script>

<style scoped>
.agent-activity-image {
  margin: 0;
  display: grid;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
}

.agent-activity-image__frame {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  min-width: 40px;
  min-height: 32px;
  max-width: 100%;
  max-height: 200px;
  overflow: hidden;
  border: 1px solid var(--border-activity-item);
  border-radius: 8px;
  background: var(--surface-activity-item);
  color: var(--muted);
  font-size: 0.72rem;
  cursor: zoom-in;
}

.agent-activity-image__frame[data-state='loaded'] {
  cursor: zoom-in;
}

.agent-activity-image__frame[data-state='error'] {
  cursor: default;
  border-color: var(--danger);
  border-style: dashed;
}

.agent-activity-image__img {
  display: block;
  max-width: 100%;
  max-height: 200px;
  object-fit: contain;
}

.agent-activity-image__hint,
.agent-activity-image__error {
  padding: 8px 10px;
  line-height: 1.3;
  text-align: center;
}

.agent-activity-image__error {
  color: var(--danger);
}

.agent-activity-image__meta {
  font-size: 0.7rem;
  color: var(--muted);
  overflow-wrap: anywhere;
}

/*
 * z-index 必须**低于**顶部拖动带（`App.vue` 里 `z-index: 200`，见 #319 的
 * `titlebarDragRegionStacking` 守护）：macOS 的窗口拖动完全依赖那条带的命中层级，
 * 任何 `inset: 0` 遮罩压过去都会让窗口拖不动。所以这里取 150——高于普通弹窗
 * （40～80），又不碰拖动带。
 */
.agent-activity-image__lightbox {
  position: fixed;
  inset: 0;
  z-index: 150;
  display: grid;
  place-items: center;
  padding: 4vh 4vw;
  background: rgba(0, 0, 0, 0.78);
}

.agent-activity-image__lightbox-img {
  max-width: 92vw;
  max-height: 88vh;
  object-fit: contain;
  border-radius: 6px;
  background: var(--surface-activity-item);
}

/* 关闭按钮放右下角：顶部 28px 是拖动带（层级更高），放上面会被压住点不到。 */
.agent-activity-image__lightbox-close {
  position: fixed;
  right: 16px;
  bottom: 16px;
  padding: 6px 14px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.4);
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  font-size: 0.8rem;
  cursor: pointer;
}
</style>
