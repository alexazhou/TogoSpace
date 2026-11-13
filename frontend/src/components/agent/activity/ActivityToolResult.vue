<template>
  <p class="agent-activity-item__tool-result">{{ content }}</p>
</template>

<script setup lang="ts">
/**
 * 普通工具结果块（#213 从 AgentActivityItem 内联模板拆出）。
 * 样式由父组件 `:deep(.agent-activity-item__tool-result)` 穿透提供。
 */
defineProps<{
  content: string;
}>();
</script>
