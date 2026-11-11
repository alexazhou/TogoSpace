<script setup lang="ts">
import { computed } from 'vue';
import { useI18n } from 'vue-i18n';
import type { AgentActivity } from '../../types';
import { useAnimatedNumber } from '../../composables/useAnimatedNumber';
import { useGrowingScale } from '../../composables/useGrowingScale';

/**
 * 活动条目尾部的 token 显示块。
 *
 * 从 `AgentActivityItem` 拆出来，收拢三件事：metadata → 显示数据的推导、数值补间与
 * 增长放大、箭头/数字的渲染。父组件只需要把 `activity` 传进来——由本组件自己判断有没有
 * token 信息，没有时整块不渲染（根节点 `v-if`）。
 */

const { t } = useI18n();

const props = defineProps<{
  activity: AgentActivity;
}>();

/**
 * split 分支的一段：值 + 「这段是估算还是真值」。
 *
 * 估算标记必须**逐段**，不能整条一个 boolean：可以出现「输入已是 usage 真值、输出还只有
 * 流中估算」的混色态，整条判定会让估算值按真值色显示，被用户当成权威数字。
 */
interface ActivityTokenSegment {
  /** 该段的数值（未格式化；格式化在模板里做，好让补间后的中间值也走同一套文案） */
  value: number;
  /** `true` 表示这段用的是估算值（后端 usage 尚未给出该段真值） */
  estimated: boolean;
}

/**
 * 活动条目尾部的 token 显示数据。
 *
 * 为什么不再返回 `string`：一个字符串承载不了「这两个数是估算还是真值」，估算态就没法在
 * 模板上挂 class 做浅色。所以拆成结构化对象，由模板分段渲染、按 `estimated` 挂 class。
 *
 * - `kind: 'text'`：单一文案（COMPACT 两条分支），与改造前逐字相同。
 * - `kind: 'split'`：推理（LLM_INFER）的「↑ 输入 ↓ 输出」两段。
 */
interface ActivityTokenDisplay {
  kind: 'text' | 'split';
  /** 完整显示文本：COMPACT 分支直接渲染，split 分支用作 `title`（补间中也是目标文案） */
  text: string;
  /** split 专用：输入段；后端没有任何输入量时缺失 */
  up?: ActivityTokenSegment;
  /** split 专用：输出段；后端没有任何输出量时缺失 */
  down?: ActivityTokenSegment;
  /** COMPACT「压缩中」专用：压缩前规模（恒定，不参与补间） */
  compactBefore?: number;
  /** COMPACT「压缩中」专用：摘要输出量（补间对象）；形态二没有这个字段 */
  compactCount?: number;
}

function activityTokenDisplay(activity: AgentActivity): ActivityTokenDisplay | null {
  // ⚠️ 所有 metadata token 键名只出现在这个函数里。
  // #169 起真值三键与上游 `OpenAIUsage` 同名（`prompt_tokens` / `completion_tokens` /
  // `total_tokens`），本侧估算用 `estimated_` 前缀，两者平铺、互不覆盖。
  const metadata = activity.metadata ?? {};
  const readNumber = (key: string): number | null =>
    typeof metadata[key] === 'number' ? (metadata[key] as number) : null;

  // COMPACT：压缩中是「压缩前规模 + 摘要输出的持续估算」，结束后是「压缩前 → 压缩后」。
  // 这两种形态已经是「两段 + 箭头」的形状，不在 token 拆分范围内，保持原样。
  const contextBefore = readNumber('context_tokens_before');
  const contextAfter = readNumber('context_tokens_after');
  const estimatedCompletion = readNumber('estimated_completion_tokens');
  const completionTokensOut = readNumber('completion_tokens');
  if (contextBefore !== null && contextAfter !== null) {
    return {
      kind: 'text',
      text: t('agent.tokenCompactRange', { before: contextBefore, after: contextAfter }),
    };
  }
  if (contextBefore !== null) {
    // 压缩中的摘要输出量：网关带 usage 的 chunk 走真值键 `completion_tokens`，
    // 不带 usage 的 chunk 走估算键 `estimated_completion_tokens`（两者可能并存，
    // 此时真值分支不累加估算器 ⇒ 真值更接近实际输出量，取真值优先，
    // 与下方 LLM_INFER 分支 `completionTokens ?? estimatedCompletion` 同一口径）。
    // 都没有时才回落到 0（仅「刚创建、尚无任何输出」这一形态）。
    //
    // `compactCount` 是补间对象（#176）：逐 chunk 递增，跳变观感与 LLM_INFER 一样差。
    // `before` 不补——压缩前规模在整个压缩过程中恒定，没有「在变」的量可补。
    const count = completionTokensOut ?? estimatedCompletion ?? 0;
    return {
      kind: 'text',
      text: t('agent.tokenCompactProgress', {
        before: contextBefore,
        count,
      }),
      compactBefore: contextBefore,
      compactCount: count,
    };
  }

  // 推理（LLM_INFER）：一律拆成「↑ 输入 ↓ 输出」。
  // 结束后用后端 usage 真值；流中用估算（`estimated_prompt_tokens` + 已输出的 completion）。
  // `total_tokens` 不参与显示（总量对「输入/输出」没有信息量，后端仍照旧写进 metadata）。
  const promptTokens = readNumber('prompt_tokens');
  const completionTokens = readNumber('completion_tokens');
  const estimatedPrompt = readNumber('estimated_prompt_tokens');
  const upValue = promptTokens ?? estimatedPrompt;
  // 结束态但 usage 只回了 prompt（网关不回 usage 时的兜底路径只写
  // `estimated_completion_tokens`）⇒ 输出量回落到流中估算的终值。
  const downValue = completionTokens ?? estimatedCompletion;
  if (upValue === null && downValue === null) {
    // 两个都没有：不渲染 tokens 元素（与改造前返回空串的行为一致）。
    return null;
  }
  // 逐段判定：某段没有 usage 真值就是估算段。混色态（输入真值 + 输出估算）必须能表达。
  const up = upValue === null ? undefined : { value: upValue, estimated: promptTokens === null };
  const down = downValue === null ? undefined : { value: downValue, estimated: completionTokens === null };
  return {
    kind: 'split',
    up,
    down,
    text: `${up ? t('agent.tokenInferenceUp', { count: up.value }) : ''}`
      + `${down ? ` ${t('agent.tokenInferenceDown', { count: down.value })}` : ''}`,
  };
}

const tokenDisplay = computed(() => activityTokenDisplay(props.activity));

/**
 * token 两段各自的补间目标值。
 *
 * 只喂 `number | null` 给 `useAnimatedNumber`，让它不感知 metadata 结构（#169 会改键名，
 * 那个改动不应波及补间实现）。
 */
const tokenUpTarget = computed<number | null>(() => {
  const display = tokenDisplay.value;
  return display && display.kind === 'split' && display.up ? display.up.value : null;
});
const tokenDownTarget = computed<number | null>(() => {
  const display = tokenDisplay.value;
  return display && display.kind === 'split' && display.down ? display.down.value : null;
});

// 两个值各自独立补间：输入与输出的推送节奏不同，共用一个进度条会出现「一段拖着另一段」。
const animatedUpTokens = useAnimatedNumber(() => tokenUpTarget.value);
const animatedDownTokens = useAnimatedNumber(() => tokenDownTarget.value);

// 增长放大只驱动视觉：哪段在变大，哪段按本次速度短暂放大再衰减，与数值补间相互独立。
const tokenUpScale = useGrowingScale(() => tokenUpTarget.value);
const tokenDownScale = useGrowingScale(() => tokenDownTarget.value);

/**
 * COMPACT「压缩中」的摘要输出量补间目标（#176）。
 *
 * 形态一 `↑ {before} ↓ {count}` 的 `count` 逐 chunk 递增，跳变观感与 LLM_INFER 的 `↓` 段
 * 完全一样；`before` 恒定不补。形态二没有 `compactCount` ⇒ 这里恒为 `null` ⇒ 不排帧，
 * 「形态二不补间」不靠额外的 if 去排除，由字段缺失天然表达。
 *
 * 形态一 → 形态二切换时 target 由数值变 `null`，`useAnimatedNumber` 只 cancel 不排帧，
 * 立刻落到形态二原文案，不留残影。
 */
const tokenCompactCountTarget = computed<number | null>(() => {
  const display = tokenDisplay.value;
  if (!display || display.kind !== 'text') {
    return null;
  }
  return display.compactCount ?? null;
});

const animatedCompactCount = useAnimatedNumber(() => tokenCompactCountTarget.value);

/**
 * 模板实际渲染的 token 视图。
 *
 * 补间只改**数字**：哪段是输入/输出、哪段是估算（浅色）全部沿用 `activityTokenDisplay`
 * 的判定，估算标记取**目标态**而不是当帧插值——估算态是数据来源的属性，与动画进度无关。
 */
const tokenView = computed(() => {
  const display = tokenDisplay.value;
  if (!display) {
    return null;
  }
  if (display.kind === 'text') {
    // COMPACT「压缩中」：`before` 恒定，只有 `count` 走补间；`title` 始终是**目标**文案
    // （`display.text`），悬停要看最终权威值而不是动画中间帧。
    // 形态二没有 `compactCount` ⇒ `tokenCompactCountTarget` 恒 `null` ⇒ `animatedCompactCount`
    // 为 `null` ⇒ 走下面的 `display.text`，不补间（也不用额外判断形态二）。
    if (display.compactBefore !== undefined && animatedCompactCount.value !== null) {
      return {
        kind: 'text' as const,
        text: t('agent.tokenCompactProgress', {
          before: display.compactBefore,
          count: animatedCompactCount.value,
        }),
        title: display.text,
      };
    }
    // 形态二 / 其余 text 分支：`title` 保持缺失（`undefined` ⇒ Vue 不渲染该属性），
    // 与改造前逐字逐属性一致 —— 补间只该影响形态一，不许顺手给形态二加 tooltip。
    return { kind: 'text' as const, text: display.text, title: undefined };
  }
  const upValue = animatedUpTokens.value;
  const downValue = animatedDownTokens.value;
  const hasUp = Boolean(display.up) && upValue !== null;
  const hasDown = Boolean(display.down) && downValue !== null;
  return {
    kind: 'split' as const,
    // 箭头与数字拆成两个节点：箭头位置固定，只有数字本身参与放大动画。
    // 空格放进箭头串里，DOM 文本与原来的「↑ 8421 ↓ 20」逐字一致。
    upArrow: hasUp ? `${t('agent.tokenInferenceUpArrow')} ` : '',
    upNumber: hasUp ? String(upValue) : '',
    downArrow: hasDown ? ` ${t('agent.tokenInferenceDownArrow')} ` : '',
    downNumber: hasDown ? String(downValue) : '',
    upEstimated: Boolean(display.up?.estimated),
    downEstimated: Boolean(display.down?.estimated),
    // `title` 用目标文案：悬停要看到的是最终权威值，不是动画中间帧。
    title: display.text,
  };
});
</script>

<template>
  <span v-if="tokenView" class="agent-activity-item__tail">
    <span
      v-if="tokenView.kind === 'text'"
      class="agent-activity-item__tokens"
      :title="tokenView.title"
    >{{ tokenView.text }}</span>
    <span
      v-else
      class="agent-activity-item__tokens"
      :title="tokenView.title"
    ><span
      v-if="tokenView.upNumber"
      class="agent-activity-item__tokens-segment"
      :class="{ 'agent-activity-item__tokens-segment--estimated': tokenView.upEstimated }"
    ><span class="agent-activity-item__tokens-arrow">{{ tokenView.upArrow }}</span><span
      class="agent-activity-item__tokens-number"
      :style="{ transform: `scale(${tokenUpScale})` }"
    >{{ tokenView.upNumber }}</span></span><span
      v-if="tokenView.downNumber"
      class="agent-activity-item__tokens-segment"
      :class="{ 'agent-activity-item__tokens-segment--estimated': tokenView.downEstimated }"
    ><span class="agent-activity-item__tokens-arrow">{{ tokenView.downArrow }}</span><span
      class="agent-activity-item__tokens-number"
      :style="{ transform: `scale(${tokenDownScale})` }"
    >{{ tokenView.downNumber }}</span></span></span>
  </span>
</template>

<style scoped>
.agent-activity-item__tokens {
  /* 父级 `.agent-activity-item__tail` 是 flex 容器，原先由 `.tail > *` 统一给子项
     `flex: none`；拆成组件后本节点不在父组件模板里，显式声明保持一致。 */
  flex: none;
  min-width: 66px;
  text-align: right;
  /* 流式 token 数字逐 chunk 递增，等宽字体保证字宽一致，避免尾部的数字左右跳动。 */
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-variant-numeric: tabular-nums;
}

/* 推理的「↑ 输入 ↓ 输出」两段：`↓` 前的前导空格写在数据侧，所以这里不加 gap
   —— 加了会让 DOM 文本与「完整文案」不一致。 */
.agent-activity-item__tokens-segment {
  display: inline-block;
  white-space: pre;
}

/* 箭头固定：位置与大小都不参与动画。 */
.agent-activity-item__tokens-arrow {
  display: inline-block;
}

/* 放大由 `useGrowingScale` 逐帧写入 inline `transform`：峰值随本次速度变化，
   再在 500ms 内衰减回 1。
   左边缘锚定：数字向右生长，箭头和左侧文字都不动，不会被「顶着往左走」。
   只动 `transform`，不动 `font-size`，不改变盒模型宽度。 */
.agent-activity-item__tokens-number {
  display: inline-block;
  transform-origin: left center;
  will-change: transform;
}

/* 估算段：流中数字还没被后端 usage 校准，用次要文字色（`--text-secondary`）与真值区分。
   逐段挂，因为可以出现「输入已是真值、输出还只有估算」的混色态。

   基础字号/颜色继承自父级的 `.agent-activity-item__row span`（本组件根节点是 tail，
   会拿到父组件的 scoped 属性），这里只覆盖估算色。失败条目不区分估算/真值
   （Operator 2026-11-09 00:45 定），失败态整行统一 danger 色，且优先级高于估算色：
   下面两条规则复现原来的特异性关系。 */
.agent-activity-item__row .agent-activity-item__tokens-segment--estimated {
  color: var(--text-secondary);
}

.agent-activity-item[data-status='failed'] .agent-activity-item__row .agent-activity-item__tokens-segment--estimated {
  color: var(--text-danger-muted);
}

.agent-activity-item[data-activity-type='tool_call'] .agent-activity-item__tokens {
  min-width: 0;
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
