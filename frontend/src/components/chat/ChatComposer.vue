<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, type StyleValue } from 'vue';
import { useI18n } from 'vue-i18n';
import { isDropInsideRect } from '../../utils/dropHitTest';
import { buildDropText, insertDroppedText } from '../../utils/dropText';
import { isTauri } from '../../utils/platform';

/**
 * 聊天输入区（含可拖拽分隔条）：从 `ChatPanel.vue` 抽出（P4）。
 *
 * 高度计算与拖拽逻辑仍在 `useChatComposerResize`（父组件调用后把 `composerStyle` 与
 * `dragging` 传下来），本组件只负责结构与交互事件，保持模板层级与 class 名不变。
 *
 * 拖入文件（#339）：两端都走我们自己的实现，不依赖内核默认行为——
 * - 浏览器：`drop` 事件里取 `DataTransfer.files[i].name`（**只有文件名，拿不到绝对路径，
 *   这是浏览器安全限制**），`preventDefault()` 阻止默认行为（否则整页会跳去打开文件）；
 * - 桌面（Tauri）：Tauri 默认 `dragDropEnabled: true` 会拦下 webview 的原生 drop
 *   （`tauri-runtime-wry` 的 drag_drop handler 恒返回 `true` ⇒ wry 不再回调 OS 默认行为），
 *   因此改为订阅 `getCurrentWebview().onDragDropEvent`，从 `payload.paths` 取**绝对路径**。
 *   同期监听 DOM `drop` 只为兜底 `preventDefault`，不做插入，避免双插。
 *
 * 落点判定（#340）：`onDragDropEvent` 是 webview 级事件，不带「命中哪个 DOM」，所以必须自己判——
 * 用 `payload.position`（物理像素）与输入框 `getBoundingClientRect()`（CSS 像素）按
 * `devicePixelRatio` 换算后比较，**未命中就什么都不做**；position / rect / DPR 任一不可用一律
 * 视为未命中（fail-closed：宁可漏插，不可乱插）。浏览器侧 DOM `drop` 天然只在本输入框上触发。
 */
const { t } = useI18n();

const props = defineProps<{
  draft: string;
  dragging: boolean;
  composerStyle: StyleValue;
  notice: string;
}>();

const emit = defineEmits<{
  updateDraft: [value: string];
  submit: [];
  resizeStart: [event: PointerEvent];
  composingChange: [value: boolean];
}>();

const textareaRef = ref<HTMLTextAreaElement | null>(null);
let stopDragDropListener: (() => void) | null = null;

/**
 * 桌面端拖放落点是否命中输入框（物理像素 ÷ DPR 后与 CSS 像素矩形比较，fail-closed）。
 *
 * 命中区域取 **textarea 本身**，不取 `.composer-editor` 整块：浏览器端 DOM `drop` 只挂在
 * textarea 上，若桌面端把底部提示/发送行也算命中，就又造出一个「拖到底部行：桌面插、浏览器不插」
 * 的不一致。要放宽必须两端一起放宽（属扩需求），不要单边改这一行。
 */
function isDropInsideComposer(position: { x: number; y: number } | null | undefined): boolean {
  const textarea = textareaRef.value;
  if (!textarea) {
    return false;
  }
  return isDropInsideRect(position, textarea.getBoundingClientRect(), window.devicePixelRatio);
}

/** 在聊天框光标处插入拖入项；桌面端传绝对路径，浏览器端传文件名。 */
function insertDroppedItems(items: readonly string[]): void {
  const element = textareaRef.value;
  const block = buildDropText(items);
  if (!element || block.length === 0) {
    return;
  }

  const { value, caret } = insertDroppedText(
    element.value,
    element.selectionStart ?? element.value.length,
    element.selectionEnd ?? element.value.length,
    block,
    // 双插保护（#339 R1）：桌面端若 webview 原生也已插入同一路径，这里不再重复插入。
    { skipIfAlreadyBeforeCaret: true },
  );
  if (value === element.value) {
    return;
  }
  emit('updateDraft', value);
  void nextTick(() => {
    element.focus();
    element.setSelectionRange(caret, caret);
  });
}

function dragCarriesFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes('Files');
}

function handleDragOver(event: DragEvent): void {
  // 不让浏览器接管拖放，否则 drop 不会触发、松手还会跳去打开文件。
  if (dragCarriesFiles(event)) {
    event.preventDefault();
  }
}

function handleDrop(event: DragEvent): void {
  if (!dragCarriesFiles(event)) {
    return;
  }
  event.preventDefault();
  if (isTauri()) {
    // 桌面端插入交给 onDragDropEvent（DOM 层拿不到真实文件路径），这里只阻止默认行为。
    // 保留这个早退而不是「两条通道都插」：`dragDropEnabled = true`（默认）时 wry 的 drag_drop
    // handler 会吞掉 drop、DOM `drop` 根本不派发；`= false` 时 handler 不安装、事件通道也不发
    // 事件——两条通道按源码是互斥的（我们**没有**真机拖拽能力实测这一点），所以早退不会漏插。
    // 若将来要把 `dragDropEnabled` 设成 false，必须同时删掉这段早退并各自补真机验证。
    return;
  }
  const names = Array.from(event.dataTransfer?.files ?? []).map((file) => file.name);
  insertDroppedItems(names);
}

onMounted(async () => {
  if (!isTauri()) {
    return;
  }
  try {
    const { getCurrentWebview } = await import('@tauri-apps/api/webview');
    // 依赖：`tauri.conf.json` 的 window `dragDropEnabled` 必须保持默认 true。
    // 显式设成 false 后 Tauri 不再安装 drag_drop handler、事件通道不再发事件，
    // 而 DOM `drop` 在 Tauri 形态下又被 `handleDrop` 早退挡掉 ⇒ 拖入会**静默失效**。
    stopDragDropListener = await getCurrentWebview().onDragDropEvent((event) => {
      if (event.payload.type !== 'drop') {
        return;
      }
      // webview 级事件无落点信息，必须自己判：拖到侧边栏/设置页时不能插进聊天草稿。
      if (!isDropInsideComposer(event.payload.position)) {
        return;
      }
      insertDroppedItems(event.payload.paths);
    });
  } catch (error) {
    console.warn('onDragDropEvent failed:', error);
  }
});

onBeforeUnmount(() => {
  stopDragDropListener?.();
  stopDragDropListener = null;
});

function handleInput(event: Event): void {
  emit('updateDraft', (event.target as HTMLTextAreaElement).value);
}

function handleEnterKey(e: KeyboardEvent): void {
  // 输入法组合态下的回车用于选词，不能当发送；keyCode 229 是 Safari/旧 WebKit 的组合态标记。
  if (e.isComposing || e.keyCode === 229) {
    return;
  }
  e.preventDefault();
  emit('submit');
}
</script>

<template>
  <form
    v-if="!notice"
    class="composer active"
    :style="composerStyle"
    @submit.prevent="emit('submit')"
  >
    <button
      type="button"
      class="composer-drag-zone"
      :class="{ dragging }"
      aria-label="调整消息区和输入框高度"
      @pointerdown="emit('resizeStart', $event)"
    >
      <span class="composer-drag-zone__grip"></span>
    </button>
    <div class="composer-editor">
      <textarea
        ref="textareaRef"
        :value="draft"
        :placeholder="t('chat.inputPlaceholder')"
        rows="2"
        @input="handleInput"
        @dragover="handleDragOver"
        @drop="handleDrop"
        @compositionstart="emit('composingChange', true)"
        @compositionend="emit('composingChange', false)"
        @keydown.enter.exact="handleEnterKey"
      ></textarea>
      <div class="composer-foot">
        <span>{{ t('chat.sendHint') }}</span>
        <button type="submit" class="composer-submit" :disabled="!draft.trim()">{{ t('chat.send') }}</button>
      </div>
    </div>
  </form>

  <div v-else class="composer-hint">{{ notice }}</div>
</template>

<style scoped>
.composer-drag-zone {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  height: var(--composer-drag-zone-height, 8px);
  padding: 0;
  border: none;
  background: transparent;
  cursor: row-resize;
  touch-action: none;
}

.composer-drag-zone__grip {
  width: 100%;
  height: 1px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--border-default) 72%, transparent);
  transition:
    background 0.18s ease,
    transform 0.18s ease;
}

.composer-drag-zone:hover .composer-drag-zone__grip,
.composer-drag-zone.dragging .composer-drag-zone__grip {
  background: color-mix(in srgb, var(--interactive-focus-border) 48%, var(--border-default) 52%);
}

.composer-drag-zone.dragging .composer-drag-zone__grip {
  transform: scaleY(1.4);
}

.composer {
  box-sizing: border-box;
  background: transparent;
  padding: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.composer-editor {
  background: var(--surface-input);
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-height: 0;
  border: 1px solid color-mix(in srgb, var(--border-subtle) 78%, var(--border-default) 22%);
  border-radius: 8px;
  overflow: hidden;
  transition:
    border-color 160ms ease,
    box-shadow 160ms ease;
}

.composer-editor:focus-within {
  border-color: var(--input-focus-border);
  box-shadow: 0 0 0 2px var(--input-focus-ring);
}

.composer textarea {
  width: 100%;
  resize: none;
  flex: 1 1 auto;
  min-height: 40px;
  border: none;
  border-radius: 0;
  padding: 12px;
  color: var(--text-primary);
  background: transparent;
  outline: none;
  font-size: 0.8rem;
  line-height: 1.3;
  display: block;
}

.composer textarea::placeholder {
  color: var(--text-secondary);
}

.composer textarea:focus {
  box-shadow: none;
}

.composer textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.composer-foot {
  position: relative;
  display: block;
  flex-shrink: 0;
  margin-top: 0;
  padding: 12px 64px 9px 12px;
  font-size: 0.74rem;
  background: transparent;
}

.composer-foot span {
  display: block;
  color: var(--text-secondary);
  line-height: 1;
}

.composer-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 5px;
  font-size: 0.74rem;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}

.composer-toggle input {
  display: none;
}

.composer-toggle.active {
  color: #f59e0b;
}

.composer-hint {
  background: transparent;
  border-top: 1px solid var(--border-subtle);
  color: var(--text-tertiary);
  text-align: center;
  padding: 3px 8px;
  font-size: 0.74rem;
}

.composer-submit {
  position: absolute;
  right: 9px;
  bottom: 9px;
  border: 0;
  border-radius: 6px;
  padding: 5px 10px;
  background: var(--interactive-selected);
  color: var(--text-primary);
  font-weight: 700;
  cursor: pointer;
  font-size: 0.74rem;
}

.composer-submit:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

:global(html.bp-layout-narrow) .chat-head,
:global(html.bp-layout-narrow) .composer-foot {
  flex-direction: column;
  align-items: flex-start;
}

:global(html.bp-compact) .composer {
  --composer-drag-zone-height: 10px;
}

:global(html.bp-compact) .composer textarea {
  min-height: 40px;
  padding: 12px 12px 10px;
  font-size: 0.86rem;
  line-height: 1.45;
}

:global(html.bp-compact) .composer-foot {
  min-height: 58px;
  padding: 10px 76px 11px 12px;
}

:global(html.bp-compact) .composer-foot span {
  line-height: 1.35;
}

:global(html.bp-compact) .composer-submit {
  right: 10px;
  bottom: 10px;
  min-width: 56px;
  min-height: 36px;
  font-size: 0.76rem;
}

:global(html.bp-compact) .composer-hint {
  padding: 8px 10px calc(8px + env(safe-area-inset-bottom, 0px));
  line-height: 1.4;
}

:global(html.bp-console-short) .composer {
  --composer-drag-zone-height: 4px;
}

:global(html.bp-console-short) .composer-editor {
  border-radius: 8px;
}

:global(html.bp-console-short) .composer textarea {
  min-height: 32px;
  padding: 8px 8px 6px;
  font-size: 0.76rem;
  line-height: 1.3;
}

:global(html.bp-console-short) .composer-foot {
  min-height: 34px;
  padding: 6px 54px 6px 8px;
  font-size: 0.64rem;
}

:global(html.bp-console-short) .composer-foot span {
  line-height: 1.2;
}

:global(html.bp-console-short) .composer-submit {
  right: 6px;
  bottom: 5px;
  min-width: 44px;
  min-height: 24px;
  font-size: 0.66rem;
}

:global(html.bp-console-short) .composer-hint {
  padding: 4px 6px;
  font-size: 0.66rem;
}
</style>
