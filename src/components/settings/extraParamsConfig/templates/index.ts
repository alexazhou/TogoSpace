import { ExtraParamsConfigTemplate } from '../types';
import { Gpt5ReasoningTemplate, GptReasoningEffortTemplate } from './gpt';
import { ClaudeThinkingBudgetTemplate } from './claude';
import { Glm52ThinkingTemplate } from './glm52';
import { GlmThinkingTemplate } from './glm5';
import { DeepseekThinkingTemplate } from './deepseek';
import { MimoReasoningTemplate } from './mimo';
import { OtherExtraParamsTemplate } from './other';

export const extraParamsConfigTemplates: ExtraParamsConfigTemplate[] = [
  new Gpt5ReasoningTemplate('gpt_5_5_pro', 'GPT-5.5 Pro', /(^|[^a-z0-9])gpt[-_]?5[._-]5[-_]?pro([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_5_5', 'GPT-5.5', /(^|[^a-z0-9])gpt[-_]?5[._-]5([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_5_4_mini', 'GPT-5.4 Mini', /(^|[^a-z0-9])gpt[-_]?5[._-]4[-_]?mini([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_5_4', 'GPT-5.4', /(^|[^a-z0-9])gpt[-_]?5[._-]4([^a-z0-9]|$)/i),
  new GptReasoningEffortTemplate(),
  new ClaudeThinkingBudgetTemplate('claude_sonnet_4_6', 'Sonnet-4.6', /sonnet.*4[._]?6/i),
  new ClaudeThinkingBudgetTemplate('claude_opus_4_6', 'Opus-4.6', /opus.*4[._]?6/i),
  new ClaudeThinkingBudgetTemplate('claude_opus_4_7', 'Opus-4.7', /opus.*4[._]?7/i),
  new ClaudeThinkingBudgetTemplate('claude_opus_4_8', 'Opus-4.8', /opus.*4[._]?8/i),
  new ClaudeThinkingBudgetTemplate('claude_legacy', 'Claude (Legacy)', /claude|anthropic/i),
  new Glm52ThinkingTemplate(),
  new GlmThinkingTemplate('glm_5_1', 'GLM-5.1'),
  new GlmThinkingTemplate('glm_5_0', 'GLM-5.0'),
  new GlmThinkingTemplate('glm_4_7', 'GLM-4.7'),
  new GlmThinkingTemplate('glm_4_6', 'GLM-4.6'),
  new DeepseekThinkingTemplate('deepseek_v4_flash', 'deepseek-v4-flash'),
  new DeepseekThinkingTemplate('deepseek_v4_pro', 'deepseek-v4-pro'),
  new MimoReasoningTemplate('mimo_v2_5', 'mimo-v2.5'),
  new MimoReasoningTemplate('mimo_v2_5_pro', 'mimo-v2.5-pro'),
  new OtherExtraParamsTemplate(),
];

export function findExtraParamsConfigTemplateById(id: string): ExtraParamsConfigTemplate {
  return extraParamsConfigTemplates.find((template) => template.id === id)
    ?? extraParamsConfigTemplates[extraParamsConfigTemplates.length - 1];
}

export function findExtraParamsConfigTemplateForModel(modelName: string): ExtraParamsConfigTemplate {
  if (modelName) {
    for (const template of extraParamsConfigTemplates) {
      if (template.id.startsWith('claude_') && template.match(modelName)) {
        return template;
      }
    }
  }

  return extraParamsConfigTemplates.find((template) => template.id !== 'other' && template.match(modelName))
    ?? findExtraParamsConfigTemplateById('other');
}
