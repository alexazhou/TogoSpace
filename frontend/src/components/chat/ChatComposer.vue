<script setup lang="ts">
import type { StyleValue } from 'vue';
import { useI18n } from 'vue-i18n';

/**
 * 聊天输入区（含可拖拽分隔条）：从 `ChatPanel.vue` 抽出（P4）。
 *
 * 高度计算与拖拽逻辑仍在 `useChatComposerResize`（父组件调用后把 `composerStyle` 与
 * `dragging` 传下来），本组件只负责结构与交互事件，保持模板层级与 class 名不变。
 */
const { t } = useI18n();

defineProps<{
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
        :value="draft"
        :placeholder="t('chat.inputPlaceholder')"
        rows="2"
        @input="handleInput"
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
