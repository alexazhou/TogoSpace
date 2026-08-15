<script setup lang="ts">
import AgentCardBase from './AgentCardBase.vue';

defineProps<{
  title: string;
  subtitle: string;
  avatarName: string;
  avatarSeed?: string;
  selected: boolean;
  variant?: 'graph';
}>();

defineEmits<{
  click: [];
}>();
</script>

<template>
  <AgentCardBase
    class="agent-card"
    :title="title"
    :subtitle="subtitle"
    :avatar-name="avatarName"
    :avatar-seed="avatarSeed"
    :selected="selected"
    :variant="variant || 'graph'"
    @click="$emit('click')"
  />
</template>
