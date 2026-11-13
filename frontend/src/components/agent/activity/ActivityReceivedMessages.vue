<template>
  <p
    v-for="(msg, idx) in messages"
    :key="idx"
    class="agent-activity-item__received-message"
  ><span class="agent-activity-item__received-sender">{{ msg.sender }}:</span> {{ msg.content }}</p>
</template>

<script setup lang="ts">
/**
 * message_received 的「收到的消息」块（#213 从 AgentActivityItem 内联模板拆出）。
 * 样式由父组件 `:deep(.agent-activity-item__received-message / __received-sender)` 穿透提供。
 */
defineProps<{
  messages: Array<{ sender: string; content: string }>;
}>();
</script>
