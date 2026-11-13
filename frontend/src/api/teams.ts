import type { RawAgentInfo, RawDeptTreeResponse, RawRoleTemplateSummary, RawTeamDetail, RawTeamSummary } from './types/raw';
import { requestJson } from './client';
import { normalizeAgent } from './normalizers/agent';
import { normalizeRoleTemplateDetail, normalizeRoleTemplateSummary, normalizeTeamDetail, normalizeTeamSummary } from './normalizers/team';
import type { AgentInfo, CreateTeamPayload, DeptTreeNode, RoleTemplateDetail, RoleTemplateSummary, TeamDetail, TeamPresetExport, TeamSummary } from '../types';

export async function saveMembersByTeamId(
  teamId: number,
  payload: Array<{
    id: number | null;
    name: string;
    role_template_id: number;
    model: string;
    driver: string;
  }>,
): Promise<AgentInfo[]> {
  const data = await requestJson<{ agents: RawAgentInfo[] }>(`/teams/${teamId}/agents/save.json`, {
    method: 'PUT',
    body: JSON.stringify({ agents: payload }),
  });
  return data.agents.map(normalizeAgent);
}

export async function getTeams(): Promise<TeamSummary[]> {
  const data = await requestJson<{ teams: RawTeamSummary[] }>('/teams/list.json');
  return data.teams.map(normalizeTeamSummary);
}

export async function getRoleTemplates(): Promise<RoleTemplateSummary[]> {
  const data = await requestJson<{ role_templates: RawRoleTemplateSummary[] }>('/role_templates/list.json');
  return data.role_templates.map(normalizeRoleTemplateSummary);
}

export async function getRoleTemplateDetail(templateId: number): Promise<RoleTemplateDetail> {
  const data = await requestJson<RawRoleTemplateSummary>(`/role_templates/${templateId}.json`);
  return normalizeRoleTemplateDetail(data, templateId);
}

export async function createRoleTemplate(payload: {
  name: string;
  soul: string;
}): Promise<RoleTemplateDetail> {
  const data = await requestJson<RawRoleTemplateSummary>('/role_templates/create.json', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return normalizeRoleTemplateDetail(data);
}

export async function updateRoleTemplate(templateId: number, payload: {
  name: string;
  soul: string;
}): Promise<RoleTemplateDetail> {
  const data = await requestJson<RawRoleTemplateSummary>(`/role_templates/${templateId}/modify.json`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return normalizeRoleTemplateDetail(data, templateId);
}

export async function deleteRoleTemplate(templateId: number): Promise<{ status: string; id: number; name: string }> {
  return requestJson(`/role_templates/${templateId}/delete.json`, {
    method: 'POST',
  });
}

export async function getTeamDetail(teamId: number): Promise<TeamDetail> {
  const data = await requestJson<RawTeamDetail>(`/teams/${teamId}.json`);
  return normalizeTeamDetail(data);
}

export async function getTeamPresetExport(teamId: number): Promise<TeamPresetExport> {
  return requestJson<TeamPresetExport>(`/teams/${teamId}/export_preset.json`);
}

export async function getDeptTree(teamId: number): Promise<DeptTreeNode | null> {
  const data = await requestJson<RawDeptTreeResponse>(`/teams/${teamId}/dept_tree.json`);
  return data.dept_tree ?? null;
}

export async function setDeptTree(teamId: number, deptTree: DeptTreeNode): Promise<{ status: string }> {
  return requestJson(`/teams/${teamId}/dept_tree/update.json`, {
    method: 'PUT',
    body: JSON.stringify({ dept_tree: deptTree }),
  });
}

export async function updateTeam(
  teamId: number,
  payload: {
    name?: string;
    working_directory?: string;
    config?: Record<string, unknown>;
    members?: Array<{
      name: string;
      role_template: string;
    }>;
    preset_rooms?: Array<{
      name: string;
      members: string[];
      initial_topic?: string | null;
      max_turns: number;
    }>;
  },
): Promise<{ status: string; name: string }> {
  return requestJson(`/teams/${teamId}/modify.json`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function setTeamEnabled(teamId: number, enabled: boolean): Promise<{ status: string; enabled: boolean }> {
  return requestJson(`/teams/${teamId}/set_enabled.json`, {
    method: 'POST',
    body: JSON.stringify({ enabled }),
  });
}

export async function deleteTeam(teamId: number): Promise<{ status: string; name: string }> {
  return requestJson(`/teams/${teamId}/delete.json`, {
    method: 'POST',
  });
}

export async function clearTeamData(teamId: number): Promise<{
  status: string;
  team_id: number;
  deleted: { tasks: number; histories: number; messages: number; rooms: number; activities: number };
}> {
  return requestJson(`/teams/${teamId}/clear_data.json`, {
    method: 'POST',
  });
}

export async function createTeam(payload: CreateTeamPayload): Promise<{ status: string; id: number; name: string }> {
  return requestJson('/teams/create.json', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
