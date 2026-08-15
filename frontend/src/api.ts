import type {
  AgentActivity,
  AgentActivityStatus,
  AgentActivityType,
  AgentTask,
  AgentTaskPriority,
  AgentTaskStatus,
  AgentStatus,
  AgentDetail,
  AgentInfo,
  CreateTeamPayload,
  DeptTreeNode,
  DirectoriesConfig,
  EntityI18n,
  FrontendConfig,
  FrontendDriverType,
  LlmProviderConfig,
  LlmModelConfig,
  LlmContextConfig,
  DefaultModelSlots,
  LlmConfigPayload,
  LlmTestResult,
  MessageInfo,
  ThirdPartySearchResult,
  ThirdPartyServicesConfigPayload,
  RoleTemplateDetail,
  RoleTemplateSummary,
  RoomInfo,
  TeamMember,
  TeamPresetExport,
  TeamRoomDetail,
  TeamDetail,
  TeamSummary,
  SkillInfo,
} from './types';
import { showGlobalRequestError, showTokenDialog } from './appUiState';
import { getToken } from './authStore';
import { t } from './i18n';
import type { AppLocale } from './i18n';

type RawRoomInfo = {
  gt_room?: {
    id?: unknown;
    name?: unknown;
    i18n?: unknown;
    type?: unknown;
    initial_topic?: unknown;
    biz_id?: unknown;
    tags?: unknown;
    max_turns?: unknown;
    agent_ids?: unknown;
  };
  state?: string;
  need_scheduling?: boolean;
  current_turn_agent_id?: unknown;
  agents?: unknown;
};

type RawAgentInfo = Partial<AgentInfo> & {
  employee_number?: number;
  status?: string;
  role_template_id?: number;
  employ_status?: string;
  driver?: string;
  special?: 'operator' | 'system' | null;
  allow_tools?: string[] | null;
  allow_skills?: string[] | null;
};

type RawAgentDetail = Partial<AgentDetail> & {
  employee_number?: number;
  status?: string;
  role_template_id?: number;
  employ_status?: string;
  driver?: string;
};

type RawAgentActivity = Partial<AgentActivity> & {
  id?: unknown;
  agent_id?: unknown;
  team_id?: unknown;
  activity_type?: unknown;
  status?: unknown;
  title?: unknown;
  detail?: unknown;
  error_message?: unknown;
  started_at?: unknown;
  finished_at?: unknown;
  duration_ms?: unknown;
  metadata?: unknown;
  created_at?: unknown;
  updated_at?: unknown;
};

type RawAgentTask = Partial<AgentTask> & {
  id?: unknown;
  team_id?: unknown;
  title?: unknown;
  description?: unknown;
  assignee_id?: unknown;
  creator_id?: unknown;
  manager_id?: unknown;
  status?: unknown;
  priority?: unknown;
  parent_id?: unknown;
  depends_on?: unknown;
  room_id?: unknown;
  result?: unknown;
  block_reason?: unknown;
  created_at?: unknown;
  updated_at?: unknown;
};

type RawDeptTreeResponse = {
  dept_tree?: DeptTreeNode | null;
};

type RawRoleTemplateSummary = Partial<RoleTemplateSummary> & {
  id?: unknown;
  name?: unknown;
  i18n?: unknown;
  soul?: unknown;
  type?: string | null;
};

type RawTeamSummary = {
  id?: unknown;
  name?: unknown;
  i18n?: unknown;
  working_directory?: unknown;
  config?: unknown;
  enabled?: unknown;
  created_at?: unknown;
  updated_at?: unknown;
};

type RawTeamMember = {
  id?: unknown;
  name?: unknown;
  i18n?: unknown;
  employee_number?: unknown;
  role_template_id?: unknown;
};

type RawTeamRoomDetail = {
  id?: unknown;
  name?: unknown;
  i18n?: unknown;
  type?: unknown;
  initial_topic?: unknown;
  max_rounds?: unknown;
  max_turns?: unknown;
  agents?: unknown;
  agent_ids?: unknown;
  biz_id?: unknown;
  tags?: unknown;
};

type RawTeamDetail = RawTeamSummary & {
  agents?: RawTeamMember[];
  rooms?: RawTeamRoomDetail[];
};
type RawFrontendDriverType = Partial<FrontendDriverType>;
type RawFrontendConfig = {
  driver_types?: RawFrontendDriverType[];
  model_slots?: { key?: string; value?: string }[];
  context_config_defaults?: { context_window_tokens?: number; reserve_output_tokens?: number; compact_trigger_ratio?: number; compact_summary_max_tokens?: number };
};

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

function makeUrl(path: string): string {
  return API_BASE_URL ? `${API_BASE_URL}${path}` : path;
}

function makeDisplayUrl(path: string): string {
  const target = makeUrl(path);
  try {
    return new URL(target, window.location.origin).toString();
  } catch {
    return target;
  }
}

function withSearch(path: string, params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();

  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value));
    }
  }

  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

function makeWsUrl(path: string): string {
  if (API_BASE_URL) {
    const url = new URL(API_BASE_URL);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    url.pathname = path;
    return url.toString();
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${path}`;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const requestUrl = makeUrl(path);
  const displayUrl = makeDisplayUrl(path);
  const token = getToken();

  // 构建请求头，自动携带 token（豁免路径除外）
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  };
  if (init?.headers) {
    // 合并传入的 headers
    const initHeaders = init.headers;
    if (initHeaders instanceof Headers) {
      initHeaders.forEach((value, key) => {
        (headers as Record<string, string>)[key] = value;
      });
    } else if (Array.isArray(initHeaders)) {
      for (const [key, value] of initHeaders) {
        (headers as Record<string, string>)[key] = value;
      }
    } else {
      Object.assign(headers, initHeaders);
    }
  }
  if (token && !isAuthExemptPath(path)) {
    (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
  }

  try {
    const response = await fetch(requestUrl, {
      headers,
      ...init,
    });
    const responseContentType = response.headers.get('content-type') || '';

    if (!response.ok) {
      let errorDetail = '';
      let errorCode = '';

      try {
        if (responseContentType.includes('application/json')) {
          const errorBody = await response.json() as {
            error_desc?: unknown;
            error_code?: unknown;
            message?: unknown;
          };
          if (typeof errorBody.error_desc === 'string' && errorBody.error_desc.trim()) {
            errorDetail = errorBody.error_desc.trim();
          } else if (typeof errorBody.message === 'string' && errorBody.message.trim()) {
            errorDetail = errorBody.message.trim();
          } else if (typeof errorBody.error_code === 'string' && errorBody.error_code.trim()) {
            errorDetail = errorBody.error_code.trim();
          }
          if (typeof errorBody.error_code === 'string' && errorBody.error_code.trim()) {
            errorCode = errorBody.error_code.trim();
          }
        } else {
          const errorText = (await response.text()).trim();
          if (errorText) {
            errorDetail = errorText;
          }
        }
      } catch {
        errorDetail = '';
      }

      // 鉴权失败：触发 token 输入
      if (response.status === 401 && (errorCode === 'auth_required' || errorCode === 'auth_invalid')) {
        showTokenDialog.value = true;
        throw new Error('Auth required');
      }

      const isProxyConnectionFailure = errorCode === 'BACKEND_UNAVAILABLE'
        || response.headers.get('x-proxy-error') === 'backend-unavailable';

      if (isProxyConnectionFailure) {
        showGlobalRequestError({
          title: t('error.cannotConnectTitle'),
          path: displayUrl,
          statusCode: response.status,
          detail: t('error.cannotConnectDetail'),
        });
        throw new Error('Backend unavailable');
      }

      showGlobalRequestError({
        title: t('error.requestFailedTitle'),
        path: displayUrl,
        statusCode: response.status,
        detail: errorDetail,
      });
      throw new Error(
        errorDetail
          ? `Request failed: ${response.status} ${errorDetail}`
          : `Request failed: ${response.status}`,
      );
    }

    if (!responseContentType.includes('application/json')) {
      const errorDetail = t('error.invalidJsonDetail');
      showGlobalRequestError({
        title: t('error.requestFailedTitle'),
        path: displayUrl,
        statusCode: response.status,
        detail: errorDetail,
      });
      throw new Error(`Invalid JSON response: ${response.status}`);
    }

    return response.json() as Promise<T>;
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('Request failed:')) {
      throw error;
    }
    if (error instanceof Error && error.message === 'Auth required') {
      throw error;
    }
    if (error instanceof Error && error.message === 'Backend unavailable') {
      throw error;
    }
    if (error instanceof Error && error.message.startsWith('Invalid JSON response:')) {
      throw error;
    }

    showGlobalRequestError({
      title: t('error.cannotConnectTitle'),
      path: displayUrl,
      detail: t('error.cannotConnectDetail'),
    });
    throw error;
  }
}

function isAuthExemptPath(path: string): boolean {
  const exemptPaths = ['/system/status.json'];
  return exemptPaths.includes(path);
}

function normalizeEntityI18n(value: unknown): EntityI18n {
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

function normalizeRoom(room: RawRoomInfo): RoomInfo {
  const gtRoom = room.gt_room;
  const roomName = String(gtRoom?.name ?? '');
  const roomType = String(gtRoom?.type ?? 'group').toLowerCase();
  const currentTurnAgentId = Number(room.current_turn_agent_id ?? 0);

  return {
    room_id: Number(gtRoom?.id ?? 0),
    room_name: roomName,
    i18n: normalizeEntityI18n(gtRoom?.i18n),
    room_type: roomType === 'private' ? 'private' : 'group',
    state: (room.state ?? 'idle').toLowerCase(),
    need_scheduling: Boolean(room.need_scheduling),
    agents: Array.isArray(room.agents)
      ? room.agents
        .filter((agent) => agent !== null && agent !== undefined)
        .map((agent) => typeof agent === 'number' ? agent : Number(agent))
        .filter((id): id is number => Number.isFinite(id) && id !== -2)
      : [],
    tags: Array.isArray(gtRoom?.tags)
      ? gtRoom.tags.filter((tag): tag is string => typeof tag === 'string')
      : [],
    biz_id: typeof gtRoom?.biz_id === 'string' && gtRoom.biz_id.trim() ? gtRoom.biz_id : null,
    current_turn_agent_id: Number.isFinite(currentTurnAgentId) && currentTurnAgentId > 0
      ? currentTurnAgentId
      : null,
  };
}

function normalizeAgentStatus(status?: string): AgentStatus {
  const normalized = status?.toLowerCase();
  if (normalized === 'active' || normalized === 'failed') {
    return normalized;
  }
  return 'idle';
}

function normalizeActivityType(value?: unknown): AgentActivityType {
  return (String(value ?? '').trim().toLowerCase() || 'unknown') as AgentActivityType;
}

function normalizeActivityStatus(value?: unknown): AgentActivityStatus {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'started' || normalized === 'succeeded' || normalized === 'failed') {
    return normalized;
  }
  return 'cancelled';
}

function normalizeDriverTypeValue(value?: string | null): string {
  const normalized = String(value ?? '').trim().toLowerCase();
  if (normalized === 'native' || normalized === 'claude_sdk' || normalized === 'tsp') {
    return normalized;
  }
  return '';
}

function normalizeAgent(agent: RawAgentInfo): AgentInfo {
  const normalizedSpecial = agent.special === 'operator' || agent.special === 'system'
    ? agent.special
    : null;

  return {
    id: typeof agent.id === 'number' ? agent.id : null,
    name: String(agent.name ?? ''),
    i18n: normalizeEntityI18n(agent.i18n),
    employee_number: typeof agent.employee_number === 'number' ? agent.employee_number : null,
    role_template_id: typeof agent.role_template_id === 'number' ? agent.role_template_id : null,
    model: String(agent.model ?? ''),
    team_id: typeof agent.team_id === 'number' ? agent.team_id : null,
    status: normalizeAgentStatus(agent.status),
    employ_status: agent.employ_status ?? null,
    driver: normalizeDriverTypeValue(typeof agent.driver === 'string' ? agent.driver : ''),
    special: normalizedSpecial,
    allow_tools: Array.isArray(agent.allow_tools) ? agent.allow_tools : null,
    allow_skills: Array.isArray(agent.allow_skills) ? agent.allow_skills : null,
  };
}

function parseDriverType(detail: RawAgentDetail): string {
  if (detail.driver_type) {
    return normalizeDriverTypeValue(String(detail.driver_type));
  }

  return normalizeDriverTypeValue(typeof detail.driver === 'string' ? detail.driver : '');
}

function normalizeAgentDetail(agent: RawAgentDetail): AgentDetail {
  return {
    ...normalizeAgent(agent),
    agent_name: String(agent.agent_name ?? agent.name ?? ''),
    driver_type: parseDriverType(agent),
    prompt: String(agent.prompt ?? ''),
    error_message: typeof agent.error_message === 'string' ? agent.error_message : null,
  };
}

function normalizeAgentActivity(activity: RawAgentActivity): AgentActivity {
  return {
    id: Number(activity.id ?? 0),
    agent_id: Number(activity.agent_id ?? 0),
    team_id: Number(activity.team_id ?? 0),
    activity_type: normalizeActivityType(activity.activity_type),
    status: normalizeActivityStatus(activity.status),
    title: String(activity.title ?? ''),
    detail: typeof activity.detail === 'string' ? activity.detail : '',
    error_message: typeof activity.error_message === 'string' ? activity.error_message : null,
    started_at: typeof activity.started_at === 'string' ? activity.started_at : null,
    finished_at: typeof activity.finished_at === 'string' ? activity.finished_at : null,
    duration_ms: typeof activity.duration_ms === 'number' ? activity.duration_ms : null,
    metadata: typeof activity.metadata === 'object' && activity.metadata !== null
      ? activity.metadata as Record<string, unknown>
      : {},
    created_at: typeof activity.created_at === 'string' ? activity.created_at : null,
    updated_at: typeof activity.updated_at === 'string' ? activity.updated_at : null,
  };
}

function normalizeAgentTaskStatus(value?: unknown): AgentTaskStatus {
  const normalized = String(value ?? '').trim().toUpperCase();
  if (
    normalized === 'TODO'
    || normalized === 'PENDING'
    || normalized === 'IN_PROGRESS'
    || normalized === 'REVIEWING'
    || normalized === 'ON_HOLD'
    || normalized === 'DONE'
    || normalized === 'CANCELLED'
  ) {
    return normalized;
  }
  return 'TODO';
}

function normalizeAgentTaskPriority(value?: unknown): AgentTaskPriority {
  const normalized = String(value ?? '').trim().toUpperCase();
  if (normalized === 'HIGH' || normalized === 'LOW') {
    return normalized;
  }
  return 'NORMAL';
}

function normalizeAgentTask(task: RawAgentTask): AgentTask {
  return {
    id: Number(task.id ?? 0),
    team_id: Number(task.team_id ?? 0),
    title: String(task.title ?? ''),
    description: typeof task.description === 'string' ? task.description : '',
    assignee_id: Number(task.assignee_id ?? 0),
    creator_id: Number(task.creator_id ?? 0),
    manager_id: typeof task.manager_id === 'number' ? task.manager_id : null,
    status: normalizeAgentTaskStatus(task.status),
    priority: normalizeAgentTaskPriority(task.priority),
    parent_id: typeof task.parent_id === 'number' ? task.parent_id : null,
    depends_on: Array.isArray(task.depends_on)
      ? task.depends_on
        .map((item) => Number(item))
        .filter((item) => Number.isFinite(item) && item > 0)
      : [],
    room_id: typeof task.room_id === 'number' ? task.room_id : null,
    result: typeof task.result === 'string' ? task.result : '',
    block_reason: typeof task.block_reason === 'string' ? task.block_reason : '',
    created_at: typeof task.created_at === 'string' ? task.created_at : null,
    updated_at: typeof task.updated_at === 'string' ? task.updated_at : null,
  };
}

function normalizeTeamSummary(team: RawTeamSummary): TeamSummary {
  return {
    id: Number(team.id ?? 0),
    name: String(team.name ?? ''),
    i18n: normalizeEntityI18n(team.i18n),
    working_directory: typeof team.working_directory === 'string' ? team.working_directory : '',
    config: typeof team.config === 'object' && team.config !== null
      ? team.config as Record<string, unknown>
      : {},
    max_function_calls: null,
    enabled: Boolean(team.enabled),
    created_at: String(team.created_at ?? ''),
    updated_at: String(team.updated_at ?? ''),
  };
}

function normalizeTeamMember(member: RawTeamMember): TeamMember {
  return {
    id: Number(member.id ?? 0),
    name: String(member.name ?? ''),
    i18n: normalizeEntityI18n(member.i18n),
    employee_number: Number(member.employee_number ?? 0),
    role_template_id: Number(member.role_template_id ?? 0),
  };
}

function normalizeTeamRoomDetail(room: RawTeamRoomDetail): TeamRoomDetail {
  const rawMaxTurns = typeof room.max_rounds === 'number'
    ? room.max_rounds
    : (typeof room.max_turns === 'number' ? room.max_turns : null);
  return {
    id: Number(room.id ?? 0),
    name: String(room.name ?? ''),
    i18n: normalizeEntityI18n(room.i18n),
    type: typeof room.type === 'string' ? room.type : undefined,
    initial_topic: typeof room.initial_topic === 'string' ? room.initial_topic : null,
    max_turns: rawMaxTurns,
    agents: Array.isArray(room.agents)
      ? room.agents.map((agent) => String(agent))
      : [],
    agent_ids: Array.isArray(room.agent_ids)
      ? room.agent_ids.map((agentId) => Number(agentId)).filter((agentId) => Number.isFinite(agentId))
      : [],
    biz_id: typeof room.biz_id === 'string' && room.biz_id.trim() ? room.biz_id : null,
    tags: Array.isArray(room.tags)
      ? room.tags.filter((tag): tag is string => typeof tag === 'string')
      : [],
  };
}

function normalizeTeamDetail(team: RawTeamDetail): TeamDetail {
  return {
    ...normalizeTeamSummary(team),
    members: Array.isArray(team.agents) ? team.agents.map(normalizeTeamMember) : [],
    rooms: Array.isArray(team.rooms) ? team.rooms.map(normalizeTeamRoomDetail) : [],
  };
}

function normalizeRoleTemplateSummary(template: RawRoleTemplateSummary): RoleTemplateSummary {
  return {
    id: Number(template.id ?? 0),
    name: String(template.name ?? ''),
    i18n: normalizeEntityI18n(template.i18n),
    soul: String(template.soul ?? ''),
    type: template.type,
  };
}

function normalizeRoleTemplateDetail(template: RawRoleTemplateSummary, templateId?: number): RoleTemplateDetail {
  return {
    ...normalizeRoleTemplateSummary(template),
    id: Number(template.id ?? templateId ?? 0),
    soul: String(template.soul ?? ''),
  };
}

export async function getAgents(): Promise<AgentInfo[]> {
  const data = await requestJson<{ agents: RawAgentInfo[] }>('/agents/list.json');
  return data.agents.map(normalizeAgent);
}

export async function getAgentsByTeamId(teamId: number, options?: { includeSpecial?: boolean }): Promise<AgentInfo[]> {
  const data = await requestJson<{ agents: RawAgentInfo[] }>(
    withSearch('/agents/list.json', {
      team_id: teamId,
      include_special: options?.includeSpecial ? 1 : undefined,
    }),
  );
  return data.agents.map(normalizeAgent);
}

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

export async function getRooms(teamId?: number): Promise<RoomInfo[]> {
  const data = await requestJson<{ rooms: RawRoomInfo[] }>(
    withSearch('/rooms/list.json', { team_id: teamId }),
  );
  return data.rooms.map(normalizeRoom);
}

export async function createTeamRoom(teamId: number, payload: {
  name: string;
  agent_ids: number[];
  initial_topic?: string | null;
  max_turns?: number;
}): Promise<{ status: string; room_name: string }> {
  const { max_turns, ...rest } = payload;
  return requestJson(`/teams/${teamId}/rooms/create.json`, {
    method: 'POST',
    body: JSON.stringify({
      initial_topic: null,
      ...rest,
      ...(max_turns !== undefined ? { max_rounds: max_turns } : {}),
    }),
  });
}

export async function updateTeamRoom(teamId: number, roomId: number, payload: {
  name?: string;
}): Promise<{ status: string; room_name: string }> {
  return requestJson(`/teams/${teamId}/rooms/${roomId}/modify.json`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
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

export async function clearAgentData(agentId: number): Promise<{
  status: string;
  agent_id: number;
  deleted: { histories: number };
}> {
  return requestJson(`/agents/${agentId}/clear_data.json`, {
    method: 'POST',
  });
}

export async function createTeam(payload: CreateTeamPayload): Promise<{ status: string; id: number; name: string }> {
  return requestJson('/teams/create.json', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function getAgentDetail(agentId: number): Promise<AgentDetail> {
  const data = await requestJson<RawAgentDetail>(`/agents/${agentId}.json`);
  return normalizeAgentDetail(data);
}

export function getAgentControlRoom(agentId: number): Promise<{ room_id: number; created: boolean }> {
  return requestJson<{ room_id: number; created: boolean }>(`/agents/${agentId}/control_room.json`);
}

export type GetAgentActivitiesOptions = {
  limit?: number;
  beforeId?: number | null;
};

export type AgentActivitiesPage = {
  activities: AgentActivity[];
  hasMore: boolean;
};

export async function getAgentActivitiesPage(
  agentId: number,
  options?: GetAgentActivitiesOptions,
): Promise<AgentActivitiesPage> {
  const data = await requestJson<{
    activities: RawAgentActivity[];
    pagination?: {
      has_more?: boolean;
    };
  }>(
    withSearch(`/agents/${agentId}/activities.json`, {
      exclude: 'AGENT_STATE',
      limit: options?.limit,
      before_id: options?.beforeId,
    }),
  );
  return {
    activities: (data.activities ?? []).map(normalizeAgentActivity),
    hasMore: Boolean(data.pagination?.has_more),
  };
}

export async function getAgentActivities(agentId: number): Promise<AgentActivity[]> {
  const page = await getAgentActivitiesPage(agentId);
  return page.activities;
}

export async function getAgentTasks(agentId: number, includeClosed = false): Promise<AgentTask[]> {
  const data = await requestJson<{ tasks: RawAgentTask[] }>(
    withSearch(`/agents/${agentId}/tasks.json`, { include_closed: includeClosed ? 1 : 0, limit: 30 }),
  );
  return (data.tasks ?? []).map(normalizeAgentTask);
}

export async function getTeamTasks(teamId: number, includeClosed = true, limit = 500): Promise<AgentTask[]> {
  const data = await requestJson<{ tasks: RawAgentTask[] }>(
    withSearch(`/teams/${teamId}/tasks.json`, { include_closed: includeClosed ? 1 : 0, limit }),
  );
  return (data.tasks ?? []).map(normalizeAgentTask);
}

export async function resumeAgent(agentId: number): Promise<{ status: string; agent_id: number; room_id: number }> {
  return requestJson(`/agents/${agentId}/resume.json`, {
    method: 'POST',
  });
}

export async function stopAgent(agentId: number): Promise<{ status: string; agent_id: number }> {
  return requestJson(`/agents/${agentId}/stop.json`, {
    method: 'POST',
  });
}

export type RawMessageInfo = {
  id: number;
  room_id?: number;
  sender_id: number;
  content: string;
  send_time: string;
  seq: number | null;
  insert_immediately: boolean;
};

export type RawRoomLastMessageInfo = RawMessageInfo & {
  room_id: number;
};

export type GetRoomMessagesOptions = {
  limit?: number;
  beforeId?: number | null;
};

export type RoomMessagesPage = {
  messages: RawMessageInfo[];
  hasMore: boolean;
};

export async function getRoomMessages(
  roomId: number,
  options?: GetRoomMessagesOptions,
): Promise<RoomMessagesPage> {
  const data = await requestJson<{
    messages: RawMessageInfo[];
    pagination?: {
      has_more?: boolean;
    };
  }>(
    withSearch(`/rooms/${roomId}/messages/list.json`, {
      limit: options?.limit,
      before_id: options?.beforeId,
    }),
  );
  return {
    messages: data.messages ?? [],
    hasMore: Boolean(data.pagination?.has_more),
  };
}

export async function getRoomLastMessages(roomIds: number[]): Promise<RawRoomLastMessageInfo[]> {
  const data = await requestJson<{
    messages: RawRoomLastMessageInfo[];
  }>('/rooms/last_messages.json', {
    method: 'POST',
    body: JSON.stringify({ room_ids: roomIds }),
  });
  return data.messages ?? [];
}

export async function postRoomMessage(roomId: number, content: string, insertImmediately = false): Promise<void> {
  await requestJson(`/rooms/${roomId}/messages/send.json`, {
    method: 'POST',
    body: JSON.stringify({ content, insert_immediately: insertImmediately }),
  });
}

export async function escalateMessageToImmediate(roomId: number, msgId: number): Promise<void> {
  await requestJson(`/rooms/${roomId}/messages/${msgId}/escalate_to_immediate.json`, {
    method: 'POST',
  });
}

export function createEventsSocket(): WebSocket {
  return new WebSocket(makeWsUrl('/ws/events.json'));
}

// ── System Status & Quick Init (V13) ──

export interface SystemStatus {
  initialized: boolean;
  auth_enabled?: boolean;
  default_llm_server?: string;
  message?: string;
  schedule_state?: 'STOPPED' | 'BLOCKED' | 'RUNNING' | 'stopped' | 'blocked' | 'running';
  not_running_reason?: string;
  development_mode?: boolean;
  version?: string;
  auto_check_update?: boolean;
}

export async function checkSystemStatus(): Promise<{ initialized: boolean }> {
  return await requestJson<{ initialized: boolean }>('/system/status.json');
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

export async function updateAgentProperties(
  agentId: number,
  payload: { model?: string; allow_tools?: string[] | null; allow_skills?: string[] | null }
): Promise<AgentDetail> {
  const data = await requestJson<RawAgentDetail>(`/agents/${agentId}/modify_properties.json`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return normalizeAgentDetail(data);
}

export async function getSystemStatus(): Promise<SystemStatus> {
  return requestJson<SystemStatus>('/system/status.json');
}

export async function resumeSchedule(): Promise<{
  status: string;
  schedule_state: 'STOPPED' | 'BLOCKED' | 'RUNNING' | 'stopped' | 'blocked' | 'running';
  not_running_reason?: string;
}> {
  return requestJson('/system/schedule/resume.json', {
    method: 'POST',
  });
}

export async function backupDatabase(): Promise<{
  status: string;
  backup_path: string;
  backup_file_name: string;
}> {
  return requestJson('/system/database/backup.json', {
    method: 'POST',
  });
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

export type SuperviseResponse = {
  room_id: number;
  created: boolean;
};

export async function superviseAgent(
  agentId: number,
  content: string,
  insertImmediately = true,
): Promise<SuperviseResponse> {
  return requestJson(`/agents/${agentId}/supervise.json`, {
    method: 'POST',
    body: JSON.stringify({ content, insert_immediately: insertImmediately }),
  });
}

export async function getSkills(): Promise<SkillInfo[]> {
  const result = await requestJson<{ skills: SkillInfo[] }>(`/config/skills/list.json?_t=${Date.now()}`);
  return result.skills;
}

export interface UpdateCheckResult {
  has_update: boolean;
  current_version: string;
  latest_version: string;
  release_url: string;
  release_notes: string;
}

export async function checkUpdate(force = false): Promise<UpdateCheckResult> {
  const qs = force ? '?force=true' : '';
  return requestJson<UpdateCheckResult>(`/system/check_update.json${qs}`);
}

export async function updateSystemConfig(payload: { auto_check_update?: boolean }): Promise<{ auto_check_update: boolean }> {
  return requestJson('/system/update_config.json', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

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
