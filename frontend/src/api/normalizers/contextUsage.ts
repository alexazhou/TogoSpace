import type { AgentContextUsage } from '../../types';

export function normalizeContextUsage(raw: unknown): AgentContextUsage | null {
  if (!raw || typeof raw !== 'object') {
    return null;
  }
  const data = raw as Record<string, unknown>;
  const used = Number(data.used_tokens);
  const limit = Number(data.limit_tokens);
  const trigger = Number(data.compact_trigger_tokens);
  const windowTokens = Number(data.context_window_tokens);
  const ratio = Number(data.ratio);
  if (
    !Number.isFinite(used) || used < 0
    || !Number.isFinite(limit) || limit <= 0
    || !Number.isFinite(trigger) || trigger < 0
    || !Number.isFinite(windowTokens) || windowTokens <= 0
    || !Number.isFinite(ratio) || ratio < 0
  ) {
    return null;
  }
  return {
    used_tokens: used,
    limit_tokens: limit,
    compact_trigger_tokens: trigger,
    context_window_tokens: windowTokens,
    ratio,
  };
}
