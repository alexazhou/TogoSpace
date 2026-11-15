import { requestJson } from './client';

/** MCP 注册表条目（Web 后台形态：含完整 target 与凭证值，供配置页回显编辑）。 */
export interface McpServerEntry {
  id?: number;
  name: string;
  kind: string;
  /** 仅 kind=mcp 有意义：stdio / sse / streamable_http；其余 kind 为 null。 */
  protocol: string | null;
  target: string;
  note: string;
  config: Record<string, Record<string, string>> | null;
  enable: boolean;
  created_by?: number;
  updated_at?: string | null;
}

export interface McpServerSavePayload {
  name: string;
  kind: string;
  protocol?: string | null;
  target: string;
  note: string;
  config: Record<string, Record<string, string>> | null;
  overwrite: boolean;
}

export async function listMcpServers(): Promise<McpServerEntry[]> {
  const data = await requestJson<{ servers: McpServerEntry[]; count: number }>('/mcp/servers.json');
  return data.servers ?? [];
}

export async function saveMcpServer(payload: McpServerSavePayload): Promise<McpServerEntry> {
  return requestJson<McpServerEntry>('/mcp/servers/save.json', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function deleteMcpServer(name: string): Promise<{ deleted: unknown }> {
  return requestJson<{ deleted: unknown }>('/mcp/servers/delete.json', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
}

export async function setMcpServerEnabled(name: string, enable: boolean): Promise<McpServerEntry> {
  return requestJson<McpServerEntry>('/mcp/servers/enable.json', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, enable }),
  });
}
