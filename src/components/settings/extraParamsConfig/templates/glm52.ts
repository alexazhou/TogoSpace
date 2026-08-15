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
  readonly id = 'glm52';
  readonly labelKey = 'settings.extraParamsConfig.modelGlm52';
  readonly displayName = 'GLM-5.2';
  readonly descriptionKey = 'settings.extraParamsConfig.glm52ThinkingDesc';
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
