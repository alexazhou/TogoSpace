import type { JsonObject } from '../types';

export function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function cloneJsonObject(value: JsonObject): JsonObject {
  return JSON.parse(JSON.stringify(value)) as JsonObject;
}

export function removeKnownThinkingParams(params: JsonObject): JsonObject {
  const next = cloneJsonObject(params);
  delete next.reasoning_effort;
  delete next.thinking_effort;
  delete next.reasoning;

  if (isJsonObject(next.thinking)) {
    delete next.thinking.type;
    delete next.thinking.budget_tokens;
    if (Object.keys(next.thinking).length === 0) {
      delete next.thinking;
    }
  }

  if (isJsonObject(next.output_config)) {
    delete next.output_config.effort;
    if (Object.keys(next.output_config).length === 0) {
      delete next.output_config;
    }
  }

  if (isJsonObject(next.extra_body)) {
    delete next.extra_body.thinking;
    delete next.extra_body.reasoning_effort;
    if (Object.keys(next.extra_body).length === 0) {
      delete next.extra_body;
    }
  }

  return next;
}
