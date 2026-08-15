<template>
  <span class="info-tooltip-wrapper" :class="`info-tooltip-wrapper--${position}`">
    <i class="info-icon">i</i>
    <div class="info-tooltip">
      <slot>{{ text }}</slot>
    </div>
  </span>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  text?: string;
  position?: 'top' | 'right';
}>(), {
  position: 'top',
});
</script>

<style scoped>
.info-tooltip-wrapper {
  position: relative;
  display: inline-flex;
  margin-left: 6px;
  vertical-align: middle;
}

.info-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--surface-soft) 80%, transparent);
  border: 1px solid color-mix(in srgb, var(--panel-border) 60%, transparent);
  color: var(--muted);
  font-size: 9px;
  font-style: normal;
  font-weight: 600;
  cursor: help;
  text-transform: none;
  letter-spacing: normal;
  transition: all 0.15s ease;
}

.info-tooltip {
  position: absolute;
  width: max-content;
  max-width: 240px;
  padding: 6px 10px;
  background: var(--text-strong);
  color: var(--panel-bg);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  pointer-events: none;
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.15s ease, transform 0.15s ease, visibility 0.15s;
  z-index: 100;
  text-align: left;
  font-size: 0.75rem;
  line-height: 1.4;
  white-space: normal;
}

/* top（默认） */
.info-tooltip-wrapper--top .info-tooltip {
  bottom: calc(100% + 8px);
  left: 50%;
  transform: translateX(-50%);
}
.info-tooltip-wrapper--top .info-tooltip::after {
  content: "";
  position: absolute;
  bottom: -6px;
  left: 50%;
  transform: translateX(-50%);
  border-width: 6px 6px 0;
  border-style: solid;
  border-color: var(--text-strong) transparent transparent transparent;
}
.info-tooltip-wrapper--top:hover .info-tooltip {
  opacity: 1;
  visibility: visible;
  transform: translateX(-50%) translateY(-2px);
}

/* right */
.info-tooltip-wrapper--right .info-tooltip {
  left: calc(100% + 8px);
  top: 50%;
  transform: translateY(-50%);
}
.info-tooltip-wrapper--right .info-tooltip::after {
  content: "";
  position: absolute;
  left: -6px;
  top: 50%;
  transform: translateY(-50%);
  border-width: 6px 6px 6px 0;
  border-style: solid;
  border-color: transparent var(--text-strong) transparent transparent;
}
.info-tooltip-wrapper--right:hover .info-tooltip {
  opacity: 1;
  visibility: visible;
  transform: translateY(-50%) translateX(2px);
}

.info-tooltip-wrapper:hover .info-icon {
  background: color-mix(in srgb, var(--surface-soft) 100%, transparent);
  color: var(--text-strong);
}
</style>
