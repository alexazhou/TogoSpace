import type { RawFrontendConfig } from './types/raw';
import { requestJson } from './client';
import type { AppLocale } from '../i18n';
import type { DirectoriesConfig, FrontendConfig, SkillInfo } from '../types';

export async function getFrontendConfig(): Promise<FrontendConfig> {
  const data = await requestJson<RawFrontendConfig>('/config/frontend.json');
  return {
    driver_types: (data.driver_types ?? []).map((item) => ({
      name: String(item.name ?? ''),
      description: String(item.description ?? ''),
    })),
    model_slots: (data.model_slots ?? []).map((item) => ({
      key: String(item.key ?? ''),
      value: String(item.value ?? ''),
    })),
    context_config_defaults: {
      context_window_tokens: data.context_config_defaults?.context_window_tokens ?? 131072,
      reserve_output_tokens: data.context_config_defaults?.reserve_output_tokens ?? 16384,
      compact_trigger_ratio: data.context_config_defaults?.compact_trigger_ratio ?? 0.85,
      compact_summary_max_tokens: data.context_config_defaults?.compact_summary_max_tokens ?? 6144,
    },
  };
}

export async function getDirectories(): Promise<DirectoriesConfig> {
  return requestJson<DirectoriesConfig>('/config/directories.json');
}

export interface SkillConfig {
  name: string;
  description: string;
  is_builtin?: boolean;
}

export interface ToolConfig {
  name: string;
  category: string;
}

export async function getAvailableSkills(): Promise<SkillConfig[]> {
  const data = await requestJson<{ skills: SkillConfig[] }>('/config/skills/list.json');
  return data.skills;
}

export async function getAvailableTools(): Promise<ToolConfig[]> {
  const data = await requestJson<{ tools: ToolConfig[] }>('/config/tools/list.json');
  return data.tools;
}

export async function quickInit(payload: {
  base_url: string;
  api_key: string;
  model: string;
  type?: string;
  protocol?: string;
  extra_params?: Record<string, unknown>;
}): Promise<{ status: string; message: string; detail?: { name: string; model: string } }> {
  return requestJson('/config/quick_init.json', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function setLanguage(language: AppLocale): Promise<{ language: AppLocale }> {
  return requestJson('/config/language.json', {
    method: 'POST',
    body: JSON.stringify({ language }),
  });
}

export async function getSkills(): Promise<SkillInfo[]> {
  const result = await requestJson<{ skills: SkillInfo[] }>(`/config/skills/list.json?_t=${Date.now()}`);
  return result.skills;
}
