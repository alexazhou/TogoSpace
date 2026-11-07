import { ExtraParamsConfigTemplate } from '../types';
import type { JsonObject, VisualConfigSchema, VisualConfigState, VisualFieldValue } from '../types';
import { cloneJsonObject, removeKnownThinkingParams } from './utils';

const GLM_THINKING_EFFORT_OPTIONS = [
  { value: 'none', fallbackLabel: 'none' },
  { value: 'high', fallbackLabel: 'high' },
  { value: 'max', fallbackLabel: 'max' },
];

const GLM_THINKING_SCHEMA: VisualConfigSchema = {
  fields: [
    {
      key: 'enabled',
      control: 'switch',
      labelKey: 'settings.extraParamsConfig.thinkingEnabled',
      fallbackLabel: 'Enable Thinking',
    },
    {
      key: 'effort',
      control: 'select',
      labelKey: 'settings.extraParamsConfig.thinkingEffort',
      fallbackLabel: 'Thinking Effort',
      options: GLM_THINKING_EFFORT_OPTIONS,
      visibleWhen: { key: 'enabled', equals: true },
    },
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
 * GLM-5.3：思考参数（thinking / reasoning_effort）与 GLM-5.2 完全一致，
 * 只有模型名与文案不同。单独成类而非放宽基类 `match`：下拉列表里两者是
 * 两个独立选项，id/displayName 必须能区分。
 */
export class Glm53ThinkingTemplate extends Glm52ThinkingTemplate {
  readonly id: string = 'glm53';
  readonly labelKey: string = 'settings.extraParamsConfig.modelGlm53';
  readonly displayName: string = 'GLM-5.3';
  readonly descriptionKey: string = 'settings.extraParamsConfig.glm53ThinkingDesc';

  override match(modelName: string): boolean {
    return modelName.trim().toLowerCase().includes('glm-5.3');
  }
}
