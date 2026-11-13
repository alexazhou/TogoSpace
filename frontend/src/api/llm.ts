import { requestJson } from './client';
import type { LlmConfigPayload, LlmModelConfig, LlmProviderConfig, LlmTestResult, ThirdPartySearchResult, ThirdPartyServicesConfigPayload } from '../types';

export async function getLlmConfig(): Promise<LlmConfigPayload> {
  return requestJson<LlmConfigPayload>('/config/llm.json');
}

export async function getProviderPresets(): Promise<Record<string, { label: string; [key: string]: string }>> {
  return requestJson('/config/provider_types.json');
}

export async function saveLlmConfig(payload: LlmConfigPayload): Promise<{ status: string }> {
  return requestJson('/config/llm.json', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function testLlmProvider(payload: {
  provider: LlmProviderConfig;
  model: string | LlmModelConfig;
  protocol?: string;
}): Promise<LlmTestResult> {
  return requestJson('/config/llm_test.json', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function getThirdPartyServicesConfig(): Promise<ThirdPartyServicesConfigPayload> {
  return requestJson<ThirdPartyServicesConfigPayload>('/config/third_party_services.json');
}

export async function saveThirdPartyServicesConfig(
  payload: ThirdPartyServicesConfigPayload,
): Promise<{ status: string }> {
  return requestJson('/config/third_party_services.json', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function testDeepSeekSearchService(payload: {
  enabled: boolean;
  api_key: string;
  query: string;
}): Promise<ThirdPartySearchResult> {
  return requestJson<ThirdPartySearchResult>('/config/third_party_services/deepseek/test.json', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function testXiaomiMimoSearchService(payload: {
  enabled: boolean;
  api_key: string;
  query: string;
}): Promise<ThirdPartySearchResult> {
  return requestJson<ThirdPartySearchResult>('/config/third_party_services/xiaomi_mimo/test.json', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
