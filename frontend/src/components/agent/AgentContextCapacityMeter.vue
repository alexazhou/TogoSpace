<script setup lang="ts">
/**
 * 上下文容量进度条 —— Agent 活动弹窗左侧舞台区域，`AgentCardBase` 与状态面板之间。
 *
 * ## 色温判据用绝对值，不用 `ratio` 比 0.85
 *
 * 红区判据是 `used_tokens >= compact_trigger_tokens`，不是 `ratio >= 0.85`。
 * 原因：`trigger = floor(limit * ratio)` 向下取整，`97484 / 114688 = 84.9993%`,
 * 而自动压缩恰好在 `used >= trigger` 那一刻执行——用 ratio 比 0.85 的话，
 * 红区在数学上是个 1 token 宽的窗口，几乎永远进不去。
 * 用绝对值判，则「红区 ⇔ 已到压缩线」这个语义恒成立，且 `compact_trigger_ratio`
 * 被用户改掉后前端自动跟随，不用改代码。
 *
 * 琥珀下限用 `limit_tokens * WARN_RATIO`（纯视觉分档，留在前端当常量）。
 * 业务阈值（trigger）来自后端，视觉分档（0.70）留在前端，二者分工要分清。
 * 若用户把 `compact_trigger_ratio` 调到 < 0.70，琥珀区间自然为空（红区先到）——
 * **不做 min/clamp 兜底**，空就是对的。
 *
 * ## 顺序：先判色温，后 clamp 宽度
 *
 * `ratio` 可能 > 1（估算值 vs 配置值口径不同，真实可发生）。如果先把 ratio
 * 夹到 1 再判色温，超限状态就被抹平成「刚好满」；所以：
 * 色温用原始 `used / trigger` 判 → 宽度才 `Math.min(100, ...)`。
 */
import { computed, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import type { AgentContextUsage } from '../../types';
import { showGlobalSuccessToast } from '../../appUiState';
import { compactAgentContext } from '../../api';

const props = defineProps<{
  /** 上下文用量；`null` = 不知道（未装载 / 负 id / 配置解析失败），整条不渲染。 */
  usage: AgentContextUsage | null;
  /** Agent 忙闲状态；`active` 期间禁止手动压缩。 */
  status: string | null;
  /** 要压缩的 Agent；null 时不渲染（与 usage 为 null 同义）。 */
  agentId: number | null;
}>();

const { t } = useI18n();

/** 琥珀下限：纯视觉分档，不是业务阈值。业务阈值来自后端 `compact_trigger_tokens`。 */
const WARN_RATIO = 0.7;

const compacting = ref(false);

/**
 * 是否渲染：`agentId` 必须是正数且 `usage` 存在。
 *
 * 负 id（Operator / SYSTEM）与 `usage == null` 一律不渲染——渲染成 0% 是在
 * 断言「上下文是空的」，而真实语义是「这个数没算出来」。
 */
const visible = computed(
  () => props.agentId !== null && props.agentId > 0 && props.usage !== null,
);

/** 宽度百分比：唯一允许 clamp 的地方（见文件头「先判色温后 clamp」）。 */
const widthPercent = computed(() => {
  if (!props.usage) {
    return 0;
  }
  return Math.min(100, Math.max(0, props.usage.ratio * 100));
});

/**
 * 色温档位。**判据用绝对值**，不看 `ratio`（理由见文件头）。
 *
 * 注意这里刻意没有 `Math.min`：`used` 超过 `trigger` 之后一路都是 `danger`，
 * 不做饱和处理——「超限」和「刚好压线」在档位上等价，但 tooltip 里能看出差别。
 */
const tier = computed<'normal' | 'warn' | 'danger'>(() => {
  if (!props.usage) {
    return 'normal';
  }
  const { used_tokens: used, limit_tokens: limit, compact_trigger_tokens: trigger } = props.usage;
  if (used >= trigger) {
    return 'danger';
  }
  if (limit > 0 && used >= limit * WARN_RATIO) {
    return 'warn';
  }
  return 'normal';
});

/** 展示用百分比，四舍五入到整数（tooltip 里给一位小数与精确 token）。 */
const displayPercent = computed(() => Math.round(widthPercent.value));

const tooltip = computed(() => {
  if (!props.usage) {
    return '';
  }
  const { used_tokens: used, limit_tokens: limit, compact_trigger_tokens: trigger, context_window_tokens: window } = props.usage;
  const percent = limit > 0 ? ((used / limit) * 100).toFixed(1) : '0.0';
  const triggerPercent = limit > 0 ? ((trigger / limit) * 100).toFixed(1) : '0.0';
  return t('agent.contextUsageTooltip', {
    used: used.toLocaleString(),
    limit: limit.toLocaleString(),
    percent,
    trigger: trigger.toLocaleString(),
    triggerPercent,
    window: window.toLocaleString(),
  });
});

/** 忙碌时禁用：活跃 Turn 正在写同一份历史，此时压缩会与之打架。 */
const disabled = computed(() => compacting.value || props.status === 'active');

async function handleCompact(): Promise<void> {
  if (disabled.value || props.agentId === null) {
    return;
  }
  compacting.value = true;
  try {
    await compactAgentContext(props.agentId);
    // 成功后**不在这里写 usage**：新数值由后端 WS 广播推过来。
    // 组件自己 set 一次会与推送竞争，且如果推送先到、本地后写，会把新值覆盖成旧值。
    showGlobalSuccessToast(t('agent.contextCompactSuccess'));
  } catch {
    // 不做额外提示：`requestJson` 已经用 `showGlobalRequestError` 把后端下发的
    // `error_desc` 显示出来了——那正是「当前无足够可压缩的历史消息」这类业务文案。
    // 这里再弹一个自己的提示只会出现两条相同信息。
    // **唯一必须保证的是：失败时绝不弹成功 toast**（见下方 finally 之前没有代码）。
  } finally {
    compacting.value = false;
  }
}
</script>

<template>
  <div
    v-if="visible"
    class="agent-context-meter"
    :data-tier="tier"
    :title="tooltip"
  >
    <div class="agent-context-meter__head">
      <span class="agent-context-meter__label">{{ t('agent.contextUsage') }}</span>
      <span class="agent-context-meter__percent">{{ displayPercent }}%</span>
      <button
        type="button"
        class="agent-context-meter__compact"
        :disabled="disabled"
        :title="status === 'active' ? t('agent.contextCompactAgentActive') : t('agent.contextCompact')"
        @click="handleCompact"
      >
        {{ t('agent.contextCompact') }}
      </button>
    </div>
    <div class="agent-context-meter__track">
      <div
        class="agent-context-meter__fill"
        :style="{ width: `${widthPercent}%` }"
      ></div>
    </div>
  </div>
</template>

<style scoped>
/* 宽度随卡片：`--entity-card-width` 在 dialog 场景是 132px，
   但在列表/其他场景另有取值，写死像素会让同一个组件在两处宽度不一致。 */
.agent-context-meter {
  width: var(--entity-card-width, 132px);
  margin: 8px 0 6px;
  --meter-color: var(--accent, #6b8afd);
}

/* 色温只用 data-tier 一个开关切色值，避免「类名组合爆炸」。
   danger 用 #ef4444 / warn 用 #f59e0b，与设计稿一致。 */
.agent-context-meter[data-tier='warn'] {
  --meter-color: #f59e0b;
}

.agent-context-meter[data-tier='danger'] {
  --meter-color: #ef4444;
}

/* 顶行：文字 + 压缩按钮，常驻显示；进度条在下一行通栏。 */
.agent-context-meter__head {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 3px;
  font-size: 0.62rem;
  line-height: 1;
  color: var(--text-secondary, var(--muted));
}

.agent-context-meter__label {
  white-space: nowrap;
}

.agent-context-meter__percent {
  /* 半等宽数字：数字变化时宽度不跳 */
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: var(--meter-color);
}

.agent-context-meter__compact {
  margin-left: auto;
  padding: 0 4px;
  height: 14px;
  font-size: 0.58rem;
  line-height: 1;
  border: 1px solid currentColor;
  border-radius: 4px;
  background: transparent;
  color: var(--text-secondary, var(--muted));
  cursor: pointer;
}

.agent-context-meter__compact:hover:not(:disabled) {
  color: var(--meter-color);
}

.agent-context-meter__compact:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.agent-context-meter__track {
  height: 4px;
  border-radius: 999px;
  /* 空槽用半透明次级文字色，比 `--surface-input`（几乎等于面板色）更易辨认。 */
  background: color-mix(in srgb, var(--text-secondary) 26%, transparent);
  overflow: hidden;
}

.agent-context-meter__fill {
  height: 100%;
  border-radius: inherit;
  background: var(--meter-color);
  transition: width 300ms cubic-bezier(0.4, 0, 0.2, 1);
}
</style>
