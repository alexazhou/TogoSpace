import type { EntityI18n } from '../../types';

export function normalizeEntityI18n(value: unknown): EntityI18n {
  if (!value || typeof value !== 'object') {
    return {};
  }

  const normalized: EntityI18n = {};
  for (const [field, rawTextMap] of Object.entries(value as Record<string, unknown>)) {
    if (!rawTextMap || typeof rawTextMap !== 'object') {
      continue;
    }

    const textMap: Record<string, string> = {};
    for (const [locale, text] of Object.entries(rawTextMap as Record<string, unknown>)) {
      if (typeof text === 'string') {
        textMap[locale] = text;
      }
    }

    normalized[field] = textMap;
  }

  return normalized;
}

/** 读游标归一化：只保留「key 可转成有限整数、value 是有限数字」的条目。 */
export function normalizeAgentReadIndex(raw: unknown): Record<string, number> {
  if (!raw || typeof raw !== 'object') {
    return {};
  }
  const out: Record<string, number> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const seq = Number(value);
    if (Number.isFinite(seq)) {
      out[String(key)] = seq;
    }
  }
  return out;
}
