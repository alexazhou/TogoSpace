const TOKEN_KEY = 'teamagent_token';

// 内存优先：桌面模式注入的 token 不落盘，浏览器模式仍走 localStorage
let memoryToken: string | null = null;

export function getToken(): string | null {
  if (memoryToken) {
    return memoryToken;
  }
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string, options: { persist?: boolean } = {}): void {
  memoryToken = token;
  if (options.persist !== false) {
    localStorage.setItem(TOKEN_KEY, token);
  }
}

export function clearToken(): void {
  memoryToken = null;
  localStorage.removeItem(TOKEN_KEY);
}
