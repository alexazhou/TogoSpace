import { ExtraParamsConfigTemplate } from '../types';
import type { JsonObject, VisualConfigSchema, VisualConfigState } from '../types';
import { cloneJsonObject, removeKnownThinkingParams } from './utils';

const MIMO_SCHEMA: VisualConfigSchema = {
  fields: [
    {
      key: 'enabled',
      control: 'switch',
      labelKey: 'settings.extraParamsConfig.thinkingEnabled',
      fallbackLabel: 'Enable Thinking',
    },
  ],
};

export class MimoReasoningTemplate extends ExtraParamsConfigTemplate {
  readonly id: string;
  readonly labelKey: string;
  readonly displayName: string;
  readonly descriptionKey = 'settings.extraParamsConfig.mimoReasoningDesc';
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
    return MIMO_SCHEMA;
  }

  getDefaultState(): VisualConfigState {
    return {
      enabled: false,
    };
  }

  readVisualState(params: JsonObject, protocol: string): VisualConfigState {
    let enabled = true;
    if (params.thinking && typeof params.thinking === 'object' && params.thinking !== null) {
      const t = params.thinking as any;
      if (t.type === 'disabled') enabled = false;
      if (t.type === 'enabled') enabled = true;
    }
    return {
      enabled,
    };
  }

  writeVisualState(params: JsonObject, state: VisualConfigState, protocol: string): JsonObject {
    const next = removeKnownThinkingParams(params);
    next.thinking = { type: state.enabled ? 'enabled' : 'disabled' };
    return next;
  }
}
