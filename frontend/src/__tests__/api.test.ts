import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  clearGlobalRequestError,
  globalRequestErrors,
  showTokenDialog,
} from '../appUiState';
import { clearToken, setToken } from '../authStore';
import {
  getAgentActivities,
  getAgentActivitiesPage,
  getSystemStatus,
  getTeamPresetExport,
  getTeamTasks,
  getTeams,
  getThirdPartyServicesConfig,
  saveThirdPartyServicesConfig,
  testDeepSeekSearchService,
  testXiaomiMimoSearchService,
} from '../api';

describe('api request handling', () => {
  beforeEach(() => {
    clearGlobalRequestError();
    clearToken();
    showTokenDialog.value = false;
    vi.restoreAllMocks();
  });

  it('attaches auth headers for regular API requests', async () => {
    setToken('abc123');
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ teams: [] }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await getTeams();

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
      Authorization: 'Bearer abc123',
    });
  });

  it('skips auth headers for exempt paths', async () => {
    setToken('abc123');
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      initialized: true,
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await getSystemStatus();

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).toMatchObject({
      'Content-Type': 'application/json',
    });
    expect(init.headers).not.toHaveProperty('Authorization');
  });

  it('opens token dialog on auth_required responses', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error_code: 'auth_required',
    }), {
      status: 401,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getTeams()).rejects.toThrow('Auth required');
    expect(showTokenDialog.value).toBe(true);
    expect(globalRequestErrors.value).toHaveLength(0);
  });

  it('surfaces backend unavailable errors as global request toasts', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error_code: 'BACKEND_UNAVAILABLE',
    }), {
      status: 502,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getTeams()).rejects.toThrow('Backend unavailable');

    expect(globalRequestErrors.value).toHaveLength(1);
    expect(globalRequestErrors.value[0]?.statusCode).toBe(502);
    expect(globalRequestErrors.value[0]?.path).toContain('/teams/list.json');
  });

  it('creates connection error toasts for network failures', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error('network down'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getTeams()).rejects.toThrow('network down');

    expect(globalRequestErrors.value).toHaveLength(1);
    expect(globalRequestErrors.value[0]?.path).toContain('/teams/list.json');
    expect(globalRequestErrors.value[0]?.statusCode).toBeNull();
  });
});

describe('getAgentActivities', () => {
  beforeEach(() => {
    clearGlobalRequestError();
    clearToken();
    vi.restoreAllMocks();
  });

  it('requests the correct URL with exclude=AGENT_STATE', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ activities: [] }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await getAgentActivities(42);

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain('/agents/42/activities.json');
    expect(url).toContain('exclude=AGENT_STATE');
  });

  it('normalizes activity_type to lowercase', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      activities: [
        {
          id: 1, agent_id: 42, team_id: 1,
          activity_type: 'LLM_INFER', status: 'SUCCEEDED',
          title: '推理', detail: '', started_at: '2024-01-01T00:00:00',
        },
        {
          id: 2, agent_id: 42, team_id: 1,
          activity_type: 'TOOL_CALL', status: 'STARTED',
          title: '工具', detail: '', started_at: '2024-01-01T00:00:00',
        },
      ],
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await getAgentActivities(42);

    expect(result).toHaveLength(2);
    expect(result[0]?.activity_type).toBe('llm_infer');
    expect(result[1]?.activity_type).toBe('tool_call');
  });

  it('supports paging older activities with limit and before_id', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      activities: [],
      pagination: { has_more: true },
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await getAgentActivitiesPage(42, { limit: 50, beforeId: 123 });

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain('/agents/42/activities.json');
    expect(url).toContain('exclude=AGENT_STATE');
    expect(url).toContain('limit=50');
    expect(url).toContain('before_id=123');
    expect(result.hasMore).toBe(true);
  });
});

describe('getTeamPresetExport', () => {
  beforeEach(() => {
    clearGlobalRequestError();
    clearToken();
    vi.restoreAllMocks();
  });

  it('requests the correct export preset URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      name: 'team-a',
      config: {},
      agents: [],
      preset_rooms: [],
      auto_start: true,
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await getTeamPresetExport(42);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/teams/42/export_preset.json');
    expect(init?.method ?? 'GET').toBe('GET');
  });

  it('surfaces non-json success responses as request errors', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response('<!doctype html><html></html>', {
      status: 200,
      headers: { 'content-type': 'text/html' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getTeamPresetExport(42)).rejects.toThrow('Invalid JSON response: 200');

    expect(globalRequestErrors.value).toHaveLength(1);
    expect(globalRequestErrors.value[0]?.path).toContain('/teams/42/export_preset.json');
  });
});

describe('getTeamTasks', () => {
  beforeEach(() => {
    clearGlobalRequestError();
    clearToken();
    vi.restoreAllMocks();
  });

  it('requests the correct team tasks URL with include_closed and limit', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ tasks: [] }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await getTeamTasks(42, true, 500);

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain('/teams/42/tasks.json');
    expect(url).toContain('include_closed=1');
    expect(url).toContain('limit=500');
  });

  it('normalizes parent and dependency fields for team tasks', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      tasks: [
        {
          id: 8,
          team_id: 2,
          title: 'Ship task view',
          description: 'Build the tree view',
          assignee_id: 3,
          creator_id: 1,
          manager_id: 2,
          status: 'IN_PROGRESS',
          priority: 'HIGH',
          parent_id: 5,
          depends_on: [4, 'x', null],
          room_id: 12,
          result: '',
          block_reason: '',
          created_at: '2026-05-29T12:00:00',
          updated_at: '2026-05-29T12:30:00',
        },
      ],
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await getTeamTasks(2);

    expect(result).toHaveLength(1);
    expect(result[0]?.parent_id).toBe(5);
    expect(result[0]?.depends_on).toEqual([4]);
    expect(result[0]?.status).toBe('IN_PROGRESS');
    expect(result[0]?.priority).toBe('HIGH');
  });
});

describe('third-party service API', () => {
  beforeEach(() => {
    clearGlobalRequestError();
    clearToken();
    vi.restoreAllMocks();
  });

  it('requests third-party service config', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      third_party_services: {
        default_service: { search: 'deepseek' },
        deepseek: { enabled: false, api_key: '' },
        xiaomi_mimo: { enabled: false, api_key: '' },
      },
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await getThirdPartyServicesConfig();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/config/third_party_services.json');
    expect(init?.method ?? 'GET').toBe('GET');
    expect(result.third_party_services.deepseek.enabled).toBe(false);
  });

  it('saves third-party service config', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ok' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    await saveThirdPartyServicesConfig({
      third_party_services: {
        default_service: { search: 'xiaomi_mimo' },
        deepseek: { enabled: true, api_key: 'sk-test' },
        xiaomi_mimo: { enabled: true, api_key: 'sk-mimo-test' },
      },
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/config/third_party_services.json');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      third_party_services: {
        default_service: { search: 'xiaomi_mimo' },
        deepseek: { enabled: true, api_key: 'sk-test' },
        xiaomi_mimo: { enabled: true, api_key: 'sk-mimo-test' },
      },
    });
  });

  it('tests DeepSeek search service with current form payload', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      service: 'deepseek',
      content: '搜索结果',
      duration_ms: 12,
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await testDeepSeekSearchService({
      enabled: true,
      api_key: 'sk-test',
      query: '小米 今天 新闻',
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/config/third_party_services/deepseek/test.json');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      enabled: true,
      api_key: 'sk-test',
      query: '小米 今天 新闻',
    });
    expect(result.success).toBe(true);
  });

  it('tests Xiaomi MiMo search service with current form payload', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      service: 'xiaomi_mimo',
      content: '搜索结果',
      sources: [{ url: 'https://example.com' }],
      duration_ms: 12,
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await testXiaomiMimoSearchService({
      enabled: true,
      api_key: 'sk-mimo-test',
      query: '小米 今天 新闻',
    });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/config/third_party_services/xiaomi_mimo/test.json');
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      enabled: true,
      api_key: 'sk-mimo-test',
      query: '小米 今天 新闻',
    });
    expect(result.sources).toEqual([{ url: 'https://example.com' }]);
  });
});
