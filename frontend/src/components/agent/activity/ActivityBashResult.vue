<template>
  <p
    v-if="command"
    class="agent-activity-item__tool-result agent-activity-item__tool-result--code"
  >{{ command }}</p>
  <p
    v-if="stdout"
    class="agent-activity-item__tool-result agent-activity-item__tool-result--stdout"
  >{{ stdout }}</p>
  <p
    v-if="stderr"
    class="agent-activity-item__tool-result agent-activity-item__tool-result--stderr"
  >{{ stderr }}</p>
</template>

<script setup lang="ts">
/**
 * execute_bash 的结果块（#213 从 AgentActivityItem 内联模板拆出）。
 * 纯展示：只吃三个已格式化好的字符串，不做任何判断。
 * 样式仍由父组件的 `:deep(.agent-activity-item__tool-result*)` 负责——
 * 元素搬进子组件后父 scoped 规则不再命中，故父侧改成 `:deep()` 穿透，
 * 命中集由 `__tests__/activityStyleHitSet.test.ts` 逐 selector 计数守住。
 */
defineProps<{
  command: string;
  stdout: string;
  stderr: string;
}>();
</script>
