import { ExtraParamsConfigTemplate } from '../types';
import type { JsonObject, VisualConfigSchema, VisualConfigState, VisualFieldValue } from '../types';
import { removeKnownThinkingParams } from './utils';

type ThinkingStrength = 'low' | 'medium' | 'high';
type Gpt5ThinkingEffort = 'minimal' | 'low' | 'medium' | 'high' | 'xhigh';

const STRENGTH_OPTIONS = [
  { value: 'low', fallbackLabel: 'low' },
  { value: 'medium', fallbackLabel: 'medium' },
  { value: 'high', fallbackLabel: 'high' },
];

const THINKING_STRENGTH_SCHEMA: VisualConfigSchema = {
  fields: [
    {
      key: 'enabled',
      control: 'switch',
      labelKey: 'settings.extraParamsConfig.thinkingEnabled',
      fallbackLabel: 'Enable Thinking',
    },
    {
      key: 'strength',
      control: 'select',
      labelKey: 'settings.extraParamsConfig.thinkingStrength',
      fallbackLabel: 'Thinking Strength',
      options: STRENGTH_OPTIONS,
      visibleWhen: { key: 'enabled', equals: true },
    },
  ],
};

// These are API enum values. Keep them raw; do not localize labels.
const GPT_5_EFFORT_OPTIONS = [
  { value: 'minimal', fallbackLabel: 'minimal' },
  { value: 'low', fallbackLabel: 'low' },
  { value: 'medium', fallbackLabel: 'medium' },
  { value: 'high', fallbackLabel: 'high' },
  { value: 'xhigh', fallbackLabel: 'xhigh' },
];

const GPT_5_REASONING_SCHEMA: VisualConfigSchema = {
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
      options: GPT_5_EFFORT_OPTIONS,
      visibleWhen: { key: 'enabled', equals: true },
    },
  ],
};

function normalizeStrength(value: VisualFieldValue): ThinkingStrength {
  return value === 'low' || value === 'medium' || value === 'high'
    ? value
    : 'high';
}

function readReasoningStrength(value: unknown): ThinkingStrength | null {
  if (value === 'low' || value === 'medium' || value === 'high') {
    return value;
  }
  return null;
}

function normalizeGpt5Effort(value: VisualFieldValue): Gpt5ThinkingEffort {
  return value === 'minimal' || value === 'low' || value === 'medium' || value === 'high' || value === 'xhigh'
    ? value
    : 'medium';
}

function readGpt5Effort(value: unknown): Gpt5ThinkingEffort | 'none' | null {
  if (value === 'none' || value === 'minimal' || value === 'low' || value === 'medium' || value === 'high' || value === 'xhigh') {
    return value;
  }
  return null;
}

export class GptReasoningEffortTemplate extends ExtraParamsConfigTemplate {
  readonly id: string;
  readonly labelKey: string;
  readonly displayName: string;
  readonly descriptionKey = 'settings.extraParamsConfig.reasoningEffortDesc';
  readonly fallbackDescription = 'Writes reasoning_effort.';

  private readonly matchPattern: RegExp | null;

  constructor(id: string = 'gpt', displayName: string = 'GPT / OpenAI', matchPattern: RegExp | null = null) {
    super();
    this.id = id;
    this.labelKey = id === 'gpt' ? 'settings.extraParamsConfig.modelGpt' : `settings.extraParamsConfig.model_${id}`;
    this.displayName = displayName;
    this.matchPattern = matchPattern;
  }

  match(modelName: string): boolean {
    const source = modelName.trim().toLowerCase();
    if (this.matchPattern) {
      return this.matchPattern.test(source);
    }
    return source.includes('gpt') || source.includes('chatgpt') || /^o\d/.test(source);
  }

  getVisualSchema(): VisualConfigSchema {
    return THINKING_STRENGTH_SCHEMA;
  }

  getDefaultState(): VisualConfigState {
    return {
      enabled: false,
      strength: 'high',
    };
  }

  readVisualState(params: JsonObject, protocol: string): VisualConfigState {
    const strength = readReasoningStrength(params.reasoning_effort);
    return {
      enabled: strength !== null,
      strength: strength ?? 'high',
    };
  }

  writeVisualState(params: JsonObject, state: VisualConfigState, protocol: string): JsonObject {
    const next = removeKnownThinkingParams(params);
    if (state.enabled === true) {
      next.reasoning_effort = normalizeStrength(state.strength);
    }
    return next;
  }
}

export class Gpt5ReasoningTemplate extends ExtraParamsConfigTemplate {
  readonly id: string;
  readonly labelKey: string;
  readonly displayName: string;
  readonly descriptionKey = 'settings.extraParamsConfig.gpt5ReasoningDesc';
  readonly fallbackDescription = 'Writes reasoning.effort.';

  private readonly matchPattern: RegExp;

  constructor(id: string, displayName: string, matchPattern: RegExp) {
    super();
    this.id = id;
    this.labelKey = `settings.extraParamsConfig.model_${id}`;
    this.displayName = displayName;
    this.matchPattern = matchPattern;
  }

  match(modelName: string): boolean {
    return this.matchPattern.test(modelName.trim());
  }

  getVisualSchema(): VisualConfigSchema {
    return GPT_5_REASONING_SCHEMA;
  }

  getDefaultState(): VisualConfigState {
    return {
      enabled: true,
      effort: 'medium',
    };
  }

  readVisualState(params: JsonObject, protocol: string): VisualConfigState {
    const reasoning = params.reasoning;
    const effort = typeof reasoning === 'object' && reasoning !== null && !Array.isArray(reasoning)
      ? readGpt5Effort((reasoning as JsonObject).effort)
      : null;

    return {
      enabled: effort === null ? true : effort !== 'none',
      effort: effort === null || effort === 'none' ? 'medium' : effort,
    };
  }

  writeVisualState(params: JsonObject, state: VisualConfigState, protocol: string): JsonObject {
    const next = removeKnownThinkingParams(params);
    next.reasoning = {
      effort: state.enabled === true ? normalizeGpt5Effort(state.effort) : 'none',
    };
    return next;
  }
}
