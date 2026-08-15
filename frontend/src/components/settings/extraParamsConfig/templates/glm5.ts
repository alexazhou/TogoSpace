import { ExtraParamsConfigTemplate } from '../types';
import type { JsonObject, VisualConfigSchema, VisualConfigState } from '../types';
import { cloneJsonObject, removeKnownThinkingParams } from './utils';

const GLM_THINKING_SCHEMA: VisualConfigSchema = {
  fields: [
    {
      key: 'enabled',
      control: 'switch',
      labelKey: 'settings.extraParamsConfig.thinkingEnabled',
      fallbackLabel: 'Enable Thinking',
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

export class GlmThinkingTemplate extends ExtraParamsConfigTemplate {
  readonly id: string;
  readonly labelKey: string;
  readonly displayName: string;
  readonly descriptionKey = 'settings.extraParamsConfig.glm5Desc';
  readonly fallbackDescription = '';

  constructor(id: string, modelName: string) {
    super();
    this.id = id;
    this.labelKey = `settings.extraParamsConfig.model_${id}`;
    this.displayName = modelName;
  }

  match(modelName: string): boolean {
    const source = modelName.trim().toLowerCase();
    return source.includes(this.displayName.toLowerCase());
  }

  getVisualSchema(): VisualConfigSchema {
    return GLM_THINKING_SCHEMA;
  }

  getDefaultState(): VisualConfigState {
    return { enabled: true };
  }

  readVisualState(params: JsonObject, protocol: string): VisualConfigState {
    const enabled = readGlmThinking(params);
    return { enabled: enabled ?? true };
  }

  writeVisualState(params: JsonObject, state: VisualConfigState, protocol: string): JsonObject {
    const next = removeKnownThinkingParams(params);
    next.thinking = { type: state.enabled ? 'enabled' : 'disabled' };
    return next;
  }
}
