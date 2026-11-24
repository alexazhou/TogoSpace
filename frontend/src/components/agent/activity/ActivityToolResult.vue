<template>
  <ActivityImageResult v-if="image" :image="image" />
  <p v-else class="agent-activity-item__tool-result">{{ content }}</p>
</template>

<script setup lang="ts">
/**
 * 普通工具结果块（#213 从 AgentActivityItem 内联模板拆出）。
 * 样式由父组件 `:deep(.agent-activity-item__tool-result)` 穿透提供。
 *
 * #361 起兼任「read_image 图片块」的出口：`image` 有值时改渲染
 * `ActivityImageResult`，无值时输出与改动前**完全一致**的 `<p>`。
 * 这样图片能力不需要在父模板新起一个 `v-if` 分支（那会给每条活动多一个
 * 注释占位节点，破坏 `activityDomEquivalence` 的金标准对照）。
 * 同理**模板里不能写注释**：开发/测试构建会把模板注释保留成 DOM 注释节点，
 * 逐字符比对立刻变红（这条是实测踩出来的，不是猜的）。
 */
import ActivityImageResult from './ActivityImageResult.vue';
import type { ActivityImage } from './activityViewModel';

withDefaults(defineProps<{
  content: string;
  image?: ActivityImage | null;
}>(), {
  image: null,
});
</script>
