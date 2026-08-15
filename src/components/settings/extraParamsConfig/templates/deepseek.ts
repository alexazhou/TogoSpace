import { ExtraParamsConfigTemplate } from '../types';
import type { JsonObject, VisualConfigSchema, VisualConfigState, VisualFieldValue } from '../types';
import { cloneJsonObject } from './utils';

const DEEPSEEK_STRENGTH_OPTIONS = [
  { value: 'high', fallbackLabel: 'high' },
  { value: 'max', fallbackLabel: 'max' },
];

const DEEPSEEK_SCHEMA: VisualConfigSchema = {
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
      options: DEEPSEEK_STRENGTH_OPTIONS,
      visibleWhen: { key: 'enabled', equals: true },
    },
  ],
};

function readDeepseekThinking(params: JsonObject): boolean | null {
  let t = params.thinking as any;
  if (!t && params.extra_body && typeof params.extra_body === 'object') {
    t = (params.extra_body as any).thinking;
  }
  if (t && typeof t === 'object') {
    if (t.type === 'enabled') return true;
    if (t.type === 'disabled') return false;
  }
  return null;
}

function normalizeEffort(value: unknown): 'high' | 'max' {
  return value === 'max' ? 'max' : 'high';
}

export class DeepseekThinkingTemplate extends ExtraParamsConfigTemplate {
  readonly id: string;
  readonly labelKey: string;
  readonly displayName: string;
  readonly descriptionKey = 'settings.extraParamsConfig.deepseekThinkingDesc';
  readonly fallbackDescription = '';

  constructor(id: string, modelName: string) {
    super();
    this.id = id;
    this.labelKey = `settings.extraParamsConfig.model_${id}`;
    this.displayName = modelName;
  }

  match(modelName: string): boolean {
    return modelName.trim().toLowerCase() === this.displayName.toLowerCase();
  }

  getVisualSchema(): VisualConfigSchema {
    return DEEPSEEK_SCHEMA;
  }

  getDefaultState(): VisualConfigState {
    return {
      enabled: true,
      effort: 'high',
    };
  }

  readVisualState(params: JsonObject, protocol: string): VisualConfigState {
    const enabled = readDeepseekThinking(params);
    let effort: unknown;
    if (protocol === 'anthropic') {
      effort = (params.output_config as any)?.effort;
    } else {
      effort = params.reasoning_effort ?? (params.extra_body as any)?.reasoning_effort;
    }
    
    return {
      enabled: enabled ?? true,
      effort: normalizeEffort(effort),
    };
  }

  writeVisualState(params: JsonObject, state: VisualConfigState, protocol: string): JsonObject {
    const next = cloneJsonObject(params);
    
    delete next.reasoning_effort;
    delete next.output_config;
    delete next.thinking;
    if (next.extra_body && typeof next.extra_body === 'object') {
      delete (next.extra_body as JsonObject).thinking;
      delete (next.extra_body as JsonObject).reasoning_effort;
      if (Object.keys(next.extra_body).length === 0) {
        delete next.extra_body;
      }
    }

    if (state.enabled) {
      next.thinking = { type: 'enabled' };
      const effort = normalizeEffort(state.effort);
      if (protocol === 'anthropic') {
        next.output_config = { effort };
      } else {
        next.reasoning_effort = effort;
      }
    } else {
      next.thinking = { type: 'disabled' };
    }
    
    return next;
  }
}
