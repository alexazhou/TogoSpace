import { ExtraParamsConfigTemplate } from '../types';
import type { JsonObject, VisualConfigSchema, VisualConfigState, VisualFieldValue, VisualConfigField } from '../types';
import { isJsonObject, removeKnownThinkingParams } from './utils';

const CLAUDE_STRENGTH_OPTIONS = [
  { value: 'low', fallbackLabel: 'low' },
  { value: 'medium', fallbackLabel: 'medium' },
  { value: 'high', fallbackLabel: 'high' },
  { value: 'xhigh', fallbackLabel: 'xhigh' },
  { value: 'max', fallbackLabel: 'max' },
];

function getClaudeSchema(legacy: boolean): VisualConfigSchema {
  const fields: VisualConfigField[] = [
    {
      key: 'enabled',
      control: 'switch',
      labelKey: 'settings.extraParamsConfig.thinkingEnabled',
      fallbackLabel: 'Enable Thinking',
    },
  ];

  if (legacy) {
    fields.push({
      key: 'budgetTokens',
      control: 'number',
      labelKey: 'settings.extraParamsConfig.thinkingBudgetTokens',
      fallbackLabel: 'Thinking Budget',
      min: 1024,
      max: 64000,
      step: 1024,
      visibleWhen: { key: 'enabled', equals: true },
    });
  } else {
    fields.push({
      key: 'effort',
      control: 'select',
      labelKey: 'settings.extraParamsConfig.thinkingEffort',
      fallbackLabel: 'Thinking Effort',
      options: CLAUDE_STRENGTH_OPTIONS,
      visibleWhen: { key: 'enabled', equals: true },
    });
  }

  return { fields };
}

function normalizeBudgetTokens(value: VisualFieldValue): number | null {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
    return value;
  }
  return null;
}

function normalizeEffort(value: unknown): string {
  if (typeof value === 'string' && ['low', 'medium', 'high', 'xhigh', 'max'].includes(value)) {
    return value;
  }
  return 'high';
}

function isLegacyClaude(modelName: string): boolean {
  const source = modelName.trim().toLowerCase();
  return source.includes('claude-3-5') || source.includes('sonnet-3-5') || source.includes('opus-3') || source.includes('haiku-3') || source.includes('sonnet-4-5') || source.includes('claude-4-5');
}

export class ClaudeThinkingBudgetTemplate extends ExtraParamsConfigTemplate {
  readonly id: string;
  readonly labelKey: string;
  readonly displayName: string;
  readonly descriptionKey = 'settings.extraParamsConfig.claudeThinkingDesc';
  readonly fallbackDescription = 'Writes thinking.type (adaptive/enabled), output_config.effort and budget_tokens.';

  readonly modelName: string;
  readonly matchPattern: RegExp | null;

  constructor(id: string = 'claude', displayName: string = 'Claude', matchPattern: RegExp | null = null, modelName: string = '') {
    super();
    this.id = id;
    this.displayName = displayName;
    this.matchPattern = matchPattern;
    this.modelName = modelName || displayName;
    this.labelKey = id === 'claude' ? 'settings.extraParamsConfig.modelClaude' : `settings.extraParamsConfig.model_${id}`;
  }

  match(modelName: string): boolean {
    const source = modelName.trim().toLowerCase();
    if (this.matchPattern) {
      return this.matchPattern.test(source);
    }
    return source.includes('claude') || source.includes('anthropic');
  }

  getVisualSchema(): VisualConfigSchema {
    const legacy = isLegacyClaude(this.modelName);
    return getClaudeSchema(legacy);
  }

  getDefaultState(): VisualConfigState {
    const legacy = isLegacyClaude(this.modelName);
    return {
      enabled: false,
      budgetTokens: legacy ? 8192 : null,
      effort: legacy ? null : 'high',
    };
  }

  readVisualState(params: JsonObject, protocol: string): VisualConfigState {
    const thinking = params.thinking;
    if (!isJsonObject(thinking)) {
      return this.getDefaultState();
    }

    let effort: unknown;
    if (isJsonObject(params.output_config)) {
      effort = params.output_config.effort;
    }

    return {
      enabled: thinking.type === 'enabled' || thinking.type === 'adaptive',
      budgetTokens: typeof thinking.budget_tokens === 'number'
        ? thinking.budget_tokens
        : null,
      effort: effort ? normalizeEffort(effort) : null,
    };
  }

  writeVisualState(params: JsonObject, state: VisualConfigState, protocol: string): JsonObject {
    const next = removeKnownThinkingParams(params);
    const legacy = isLegacyClaude(this.modelName);
    
    if (state.enabled === true) {
      if (legacy) {
        next.thinking = {
          type: 'enabled',
          budget_tokens: normalizeBudgetTokens(state.budgetTokens) ?? 8192,
        };
      } else {
        next.thinking = { type: 'adaptive' };
        next.output_config = { effort: normalizeEffort(state.effort) };
      }
    }
    return next;
  }
}
