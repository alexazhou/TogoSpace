import { ExtraParamsConfigTemplate } from '../types';
import { Gpt5ReasoningTemplate, GptReasoningEffortTemplate } from './gpt';
import { ClaudeThinkingBudgetTemplate } from './claude';
import { Glm52ThinkingTemplate, Glm53FlashThinkingTemplate, Glm53ThinkingTemplate } from './glm52';
import { GlmThinkingTemplate } from './glm5';
import { DeepseekThinkingTemplate } from './deepseek';
import { MimoReasoningTemplate } from './mimo';
import { OtherExtraParamsTemplate } from './other';

export const extraParamsConfigTemplates: ExtraParamsConfigTemplate[] = [
  new Gpt5ReasoningTemplate('gpt_6_1_sol', 'GPT-6.1 Sol', /(^|[^a-z0-9])gpt[-_]?6[._-]1[-_]?sol([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_6_sol', 'GPT-6 Sol', /(^|[^a-z0-9])gpt[-_]?6[-_]?sol([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_6_astra', 'GPT-6 Astra', /(^|[^a-z0-9])gpt[-_]?6[-_]?astra([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_5_6_sol', 'GPT-5.6 Sol', /(^|[^a-z0-9])gpt[-_]?5[._-]6[-_]?sol([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_5_6_terra', 'GPT-5.6 Terra', /(^|[^a-z0-9])gpt[-_]?5[._-]6[-_]?terra([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_5_6_luna', 'GPT-5.6 Luna', /(^|[^a-z0-9])gpt[-_]?5[._-]6[-_]?luna([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_5_5_pro', 'GPT-5.5 Pro', /(^|[^a-z0-9])gpt[-_]?5[._-]5[-_]?pro([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_5_5', 'GPT-5.5', /(^|[^a-z0-9])gpt[-_]?5[._-]5([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_5_4_mini', 'GPT-5.4 Mini', /(^|[^a-z0-9])gpt[-_]?5[._-]4[-_]?mini([^a-z0-9]|$)/i),
  new Gpt5ReasoningTemplate('gpt_5_4', 'GPT-5.4', /(^|[^a-z0-9])gpt[-_]?5[._-]4([^a-z0-9]|$)/i),
  new GptReasoningEffortTemplate(),
  new ClaudeThinkingBudgetTemplate('claude_opus_5_5', 'Opus-5.5', /opus.*5[._-]?5/i),
  new ClaudeThinkingBudgetTemplate('claude_opus_5', 'Opus-5', /opus[-_ ]5([^0-9]|$)/i),
  new ClaudeThinkingBudgetTemplate('claude_opus_4_8', 'Opus-4.8', /opus.*4[._]?8/i),
  new ClaudeThinkingBudgetTemplate('claude_opus_4_7', 'Opus-4.7', /opus.*4[._]?7/i),
  new ClaudeThinkingBudgetTemplate('claude_opus_4_6', 'Opus-4.6', /opus.*4[._]?6/i),
  new ClaudeThinkingBudgetTemplate('claude_sonnet_5_5', 'Sonnet-5.5', /sonnet.*5[._-]?5/i),
  new ClaudeThinkingBudgetTemplate('claude_sonnet_5', 'Sonnet-5', /sonnet[-_ ]5([^0-9]|$)/i),
  new ClaudeThinkingBudgetTemplate('claude_sonnet_4_6', 'Sonnet-4.6', /sonnet.*4[._]?6/i),
  new ClaudeThinkingBudgetTemplate('claude_legacy', 'Claude (Legacy)', /claude|anthropic/i),
  new Glm53FlashThinkingTemplate(),
  new Glm53ThinkingTemplate(),
  new Glm52ThinkingTemplate(),
  new GlmThinkingTemplate('glm_5_1', 'GLM-5.1'),
  new GlmThinkingTemplate('glm_5_0', 'GLM-5.0'),
  new GlmThinkingTemplate('glm_4_7', 'GLM-4.7'),
  new GlmThinkingTemplate('glm_4_6', 'GLM-4.6'),
  new DeepseekThinkingTemplate('deepseek_v4_1_flash', 'deepseek-v4.1-flash'),
  new DeepseekThinkingTemplate('deepseek_v4_flash', 'deepseek-v4-flash'),
  new DeepseekThinkingTemplate('deepseek_v4_pro', 'deepseek-v4-pro'),
  new MimoReasoningTemplate('mimo_v2_6_flash', 'mimo-v2.6-flash'),
  new MimoReasoningTemplate('mimo_v2_6_pro', 'mimo-v2.6-pro'),
  new MimoReasoningTemplate('mimo_v2_6_pro_ultraspeed', 'mimo-v2.6-pro-ultraspeed'),
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
