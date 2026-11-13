<template>
  <div
    class="agent-activity-item__chat-reply"
    :class="{ 'agent-activity-item__chat-reply--collapsed': collapsed && collapsible }"
  >
    <MarkdownContent :content="content" />
  </div>
  <button
    v-if="collapsible"
    type="button"
    class="agent-activity-item__chat-reply-toggle"
    @click="emit('toggle')"
  >{{ collapsed ? t('common.expand') : t('common.collapse') }}</button>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n';
import MarkdownContent from '../../ui/MarkdownContent.vue';

/**
 * 发言/回复正文块（#213 从 AgentActivityItem 内联模板拆出）。
 * 两处用法（send_chat_msg 内容、chat_reply 正文）共用同一个折叠状态，
 * 因此折叠与否统一由父组件传入，子组件不持有状态（行为与拆分前一致）。
 * 样式由父组件 `:deep(.agent-activity-item__chat-reply*)` 穿透提供。
 */
defineProps<{
  content: string;
  /** 折叠状态（父组件的 chatReplyCollapsed） */
  collapsed: boolean;
  /** 是否值得给折叠按钮（长内容才给） */
  collapsible: boolean;
}>();

const emit = defineEmits<{
  toggle: [];
}>();

const { t } = useI18n();
</script>
