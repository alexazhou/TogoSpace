const AVATAR_COUNT = 77;

function hashString(value: string): number {
  let hash = 2166136261;

  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }

  return hash >>> 0;
}

export function getAgentAvatarIndex(agentName: string): number {
  if (!agentName) {
    return 1;
  }

  return (hashString(agentName) % AVATAR_COUNT) + 1;
}

export function getAgentAvatarUrl(agentName: string): string {
  return `/avatars/${String(getAgentAvatarIndex(agentName)).padStart(3, '0')}.png`;
}
