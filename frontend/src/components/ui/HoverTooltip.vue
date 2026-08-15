<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

const props = defineProps<{
  text: string;
  position?: 'top' | 'bottom';
}>();

const triggerRef = ref<HTMLElement | null>(null);
const show = ref(false);

const tooltipStyle = computed(() => {
  if (!triggerRef.value) return {};
  const rect = triggerRef.value.getBoundingClientRect();
  const pos = props.position ?? 'top';
  return {
    position: 'fixed',
    ...(pos === 'top'
      ? { bottom: `${window.innerHeight - rect.top + 6}px` }
      : { top: `${rect.bottom + 6}px` }),
    left: `${rect.left + rect.width / 2}px`,
    transform: 'translateX(-50%)',
  };
});

function onEnter(): void {
  show.value = true;
}

function onLeave(): void {
  show.value = false;
}
</script>

<template>
  <span
    ref="triggerRef"
    class="hover-tooltip-trigger"
    @mouseenter="onEnter"
    @mouseleave="onLeave"
  >
    <slot />
    <Teleport to="body">
      <span v-if="show" class="hover-tooltip" :style="tooltipStyle">{{ text }}</span>
    </Teleport>
  </span>
</template>

<style scoped>
.hover-tooltip-trigger {
  display: inline-flex;
  align-items: center;
}
</style>

<style>
.hover-tooltip {
  padding: 4px 8px;
  border-radius: 6px;
  background: var(--text-strong);
  color: var(--panel-bg);
  font-size: 0.72rem;
  font-weight: 400;
  white-space: nowrap;
  pointer-events: none;
  z-index: 99999;
}
</style>
