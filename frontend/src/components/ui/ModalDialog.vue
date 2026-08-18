<script setup lang="ts">
import { computed, type CSSProperties } from 'vue';

const props = withDefaults(defineProps<{
  open: boolean;
  title?: string;
  eyebrow?: string;
  width?: number;
  zIndex?: number;
  closeLabel?: string;
  scrollbar?: boolean;
}>(), {
  title: '',
  eyebrow: '',
  width: 560,
  zIndex: 80,
  closeLabel: '',
  scrollbar: true,
});

const emit = defineEmits<{
  close: [];
}>();

const overlayStyle = computed<CSSProperties>(() => ({ zIndex: props.zIndex }));
const dialogStyle = computed<CSSProperties>(
  () => ({ '--modal-width': `${props.width}px` }) as CSSProperties,
);

function handleOverlayClick(e: MouseEvent): void {
  if (e.target === e.currentTarget) {
    emit('close');
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="ui-modal__overlay"
      :style="overlayStyle"
      @click="handleOverlayClick"
    >
      <section
        class="ui-modal__dialog panel"
        :class="{ 'scrollbar-thin': scrollbar }"
        :style="dialogStyle"
      >
        <header class="ui-modal__head">
          <div class="ui-modal__head-copy">
            <div v-if="eyebrow || title" class="ui-modal__head-text">
              <p v-if="eyebrow" class="ui-modal__eyebrow">{{ eyebrow }}</p>
              <h3 v-if="title">{{ title }}</h3>
            </div>
            <slot name="head-extra" />
          </div>
          <button
            type="button"
            class="ghost-button ui-modal__close"
            :aria-label="closeLabel || undefined"
            @click="emit('close')"
          >
            ×
          </button>
        </header>

        <slot />

        <footer
          v-if="$slots.footer || $slots['footer-leading'] || $slots['footer-trailing']"
          class="ui-modal__footer"
        >
          <div v-if="$slots['footer-leading']" class="ui-modal__footer-leading">
            <slot name="footer-leading" />
          </div>
          <slot name="footer" />
          <div v-if="$slots['footer-trailing']" class="ui-modal__footer-trailing">
            <slot name="footer-trailing" />
          </div>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.ui-modal__overlay {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgba(6, 10, 16, 0.56);
  backdrop-filter: blur(10px);
}

.ui-modal__dialog {
  width: min(var(--modal-width, 560px), calc(100vw - 40px));
  max-height: calc(100vh - 40px);
  padding: 18px;
  display: grid;
  gap: 14px;
  overflow: auto;
  background: var(--panel-bg);
  border-radius: 12px;
}

.ui-modal__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.ui-modal__head-copy {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 0;
}

.ui-modal__head-text {
  min-width: 0;
}

.ui-modal__head-text h3 {
  margin: 0;
  color: var(--text-strong);
}

.ui-modal__eyebrow {
  margin: 0;
  color: var(--accent);
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-size: 0.68rem;
}

.ui-modal__close {
  flex-shrink: 0;
  min-width: 32px;
  height: 32px;
  padding: 0;
  font-size: 1rem;
}

.ui-modal__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.ui-modal__footer-leading,
.ui-modal__footer-trailing {
  display: flex;
  gap: 8px;
}

.ui-modal__footer-trailing {
  justify-content: flex-end;
}

@media (max-width: 640px) {
  .ui-modal__overlay {
    padding: 12px;
  }

  .ui-modal__dialog {
    width: min(100%, calc(100vw - 24px));
    max-height: calc(100vh - 24px);
    padding: 14px;
  }

  .ui-modal__footer {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>