import { ExtraParamsConfigTemplate } from '../types';
import type {
  JsonObject,
  VisualConfigField,
  VisualConfigSchema,
  VisualConfigState,
  VisualFieldValue,
} from '../types';
import { cloneJsonObject, isJsonObject, removeKnownThinkingParams } from './utils';

const GLM_THINKING_EFFORT_OPTIONS = [
  { value: 'none', fallbackLabel: 'none' },
  { value: 'high', fallbackLabel: 'high' },
  { value: 'max', fallbackLabel: 'max' },
];

const GLM_THINKING_EFFORT_FIELD: VisualConfigField = {
  key: 'effort',
  control: 'select',
  labelKey: 'settings.extraParamsConfig.thinkingEffort',
  fallbackLabel: 'Thinking Effort',
  options: GLM_THINKING_EFFORT_OPTIONS,
};

const GLM_THINKING_SCHEMA: VisualConfigSchema = {
  fields: [
    {
      key: 'enabled',
      control: 'switch',
      labelKey: 'settings.extraParamsConfig.thinkingEnabled',
      fallbackLabel: 'Enable Thinking',
    },
    {
      ...GLM_THINKING_EFFORT_FIELD,
      visibleWhen: { key: 'enabled', equals: true },
    },
  ],
};

/**
 * GLM-5.3 不支持关闭思考：不渲染「启用思考」开关。
 * 保留「保留思考过程」开关（对应 `thinking.clear_thinking: false`）与强度选择。
 */
const GLM53_THINKING_SCHEMA: VisualConfigSchema = {
  fields: [
    {
      key: 'keepThinking',
      control: 'switch',
      labelKey: 'settings.extraParamsConfig.keepThinkingContent',
      fallbackLabel: 'Keep Thinking Process',
    },
    GLM_THINKING_EFFORT_FIELD,
  ],
};

function readGlmThinking(params: JsonObject): boolean | null {
  if (params.thinking && typeof params.thinking === 'object' && params.thinking !== null) {
    const t = params.thinking as any;
    if (t.type === 'enabled') return true;
    if (t.type === 'disabled') return false;
  }
  return null;
}

function normalizeGlmEffort(value: VisualFieldValue): 'none' | 'high' | 'max' {
  return value === 'none' || value === 'high' || value === 'max' ? value : 'max';
}

function readGlmEffort(value: unknown): 'none' | 'high' | 'max' | null {
  if (value === 'none' || value === 'high' || value === 'max') {
    return value;
  }
  return null;
}

export class Glm52ThinkingTemplate extends ExtraParamsConfigTemplate {
  /* 类型显式标成 `string`：GLM-5.3 要复用同一套读写逻辑、只覆盖这几个字段，
     若推断成字面量类型（readonly 属性默认如此），子类覆盖会过不了类型检查。 */
  readonly id: string = 'glm52';
  readonly labelKey: string = 'settings.extraParamsConfig.modelGlm52';
  readonly displayName: string = 'GLM-5.2';
  readonly descriptionKey: string = 'settings.extraParamsConfig.glm52ThinkingDesc';
  readonly fallbackDescription = '';

  match(modelName: string): boolean {
    const source = modelName.trim().toLowerCase();
    return source.includes('glm-5.2');
  }

  getVisualSchema(): VisualConfigSchema {
    return GLM_THINKING_SCHEMA;
  }

  getDefaultState(): VisualConfigState {
    return {
      enabled: true,
      effort: 'max',
    };
  }

  readVisualState(params: JsonObject, protocol: string): VisualConfigState {
    const enabled = readGlmThinking(params);
    const effort = readGlmEffort(params.reasoning_effort) ?? readGlmEffort((params.extra_body as any)?.reasoning_effort);
    return {
      enabled: enabled ?? true,
      effort: normalizeGlmEffort(effort),
    };
  }

  writeVisualState(params: JsonObject, state: VisualConfigState, protocol: string): JsonObject {
    const next = removeKnownThinkingParams(params);
    next.thinking = { type: state.enabled ? 'enabled' : 'disabled' };
    if (state.enabled) {
      next.reasoning_effort = normalizeGlmEffort(state.effort);
    }
    return next;
  }
}

/**
 * GLM-5.3：思考参数（thinking / reasoning_effort）与 GLM-5.2 一致，但**不支持关闭思考**，
 * 且没有 `enabled` 开关——可视化面板只暴露强度选择，写入时始终落 `thinking.type=enabled`。
 * 单独成类而非放宽基类 `match`：下拉列表里两者是两个独立选项，id/displayName 必须能区分。
 */
export class Glm53ThinkingTemplate extends Glm52ThinkingTemplate {
  readonly id: string = 'glm53';
  readonly labelKey: string = 'settings.extraParamsConfig.modelGlm53';
  readonly displayName: string = 'GLM-5.3';
  readonly descriptionKey: string = 'settings.extraParamsConfig.glm53ThinkingDesc';

  override match(modelName: string): boolean {
    const source = modelName.trim().toLowerCase();
    // 排除 flash 变体：两者都含 "glm-5.3"，不排除的话在列表顺序变化时会互相抢匹配。
    return source.includes('glm-5.3') && !source.includes('glm-5.3-flash');
  }

  override getVisualSchema(): VisualConfigSchema {
    return GLM53_THINKING_SCHEMA;
  }

  override getDefaultState(): VisualConfigState {
    // 默认保留思考过程 → clear_thinking 写 false。
    return { keepThinking: true, effort: 'max' };
  }

  override readVisualState(params: JsonObject, protocol: string): VisualConfigState {
    // 历史配置里即便写着 thinking.type=disabled，GLM-5.3 也只能按开启展示（不提供关闭入口）。
    const effort = readGlmEffort(params.reasoning_effort) ?? readGlmEffort((params.extra_body as any)?.reasoning_effort);
    // `clear_thinking` 语义是「是否清除」，所以 keepThinking 取反；缺省视为保留。
    const clearThinking = isJsonObject(params.thinking) ? params.thinking.clear_thinking : undefined;
    return {
      keepThinking: clearThinking !== true,
      effort: normalizeGlmEffort(effort),
    };
  }

  override writeVisualState(params: JsonObject, state: VisualConfigState, protocol: string): JsonObject {
    const next = removeKnownThinkingParams(params);
    // 保留思考过程对应 clear_thinking: false；开关缺省时同样按保留处理。
    next.thinking = {
      type: 'enabled',
      clear_thinking: state.keepThinking === false,
    };
    next.reasoning_effort = normalizeGlmEffort(state.effort);
    return next;
  }
}

/**
 * GLM-5.3-flash：参数行为与 GLM-5.3 完全一致（思考恒开、只能调强度），
 * 仅模型名/文案不同，因此直接继承。
 */
export class Glm53FlashThinkingTemplate extends Glm53ThinkingTemplate {
  readonly id: string = 'glm53_flash';
  readonly labelKey: string = 'settings.extraParamsConfig.modelGlm53Flash';
  readonly displayName: string = 'GLM-5.3-flash';
  readonly descriptionKey: string = 'settings.extraParamsConfig.glm53FlashThinkingDesc';

  override match(modelName: string): boolean {
    return modelName.trim().toLowerCase().includes('glm-5.3-flash');
  }
}
