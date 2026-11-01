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
  getAgentDetail,
  getSystemStatus,
  getTaskDetail,
  getTaskEventsPage,
  getTeamPresetExport,
  getTeamTasks,
  getTeams,
  getThirdPartyServicesConfig,
  saveThirdPartyServicesConfig,
  testDeepSeekSearchService,
  testXiaomiMimoSearchService,
} from '../api';
// `list_agent_containers` 的真实链路取样（见该文件 _meta）。
// 用 JSON 而不是内联字面量：它要能被别的用例原样复用，且改后端的人一眼能看出
// 这是「后端产物样本」而不是前端手写的测试数据。
import baselineContainers from './fixtures/list_agent_containers.baseline.json';

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
          reviewer_id: 2,
          status: 'IN_PROGRESS',
          priority: 'HIGH',
          parent_id: 5,
          depends_on: [4, 'x', null],
          room_id: 12,
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
    expect(result[0]?.reviewer_id).toBe(2);
  });

  it('normalizes task events and detail-only fields', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      task: {
        id: 8,
        team_id: 2,
        title: 'Ship task view',
        description: '',
        assignee_id: 3,
        creator_id: 1,
        reviewer_id: 2,
        status: 'REVIEWING',
        priority: 'NORMAL',
        parent_id: null,
        depends_on: [4],
        room_id: null,
        created_at: '2026-05-29T12:00:00.123456',
        updated_at: '2026-05-29T12:30:00.654321',
        events_total: 2,
        last_note: '已就绪',
        current_block_reason: null,
        depends_on_details: [{ task_id: 4, title: '接口设计', status: 'DONE' }],
        events: [
          { id: 2, task_id: 8, actor_id: 3, from_status: 'IN_PROGRESS', to_status: 'REVIEWING', content: '已就绪', created_at: '2026-05-29T12:30:00' },
          { id: 1, task_id: 8, actor_id: 1, from_status: null, to_status: 'TODO', content: '', created_at: '2026-05-29T12:00:00' },
        ],
      },
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const task = await getTaskDetail(8);

    expect(task.reviewer_id).toBe(2);
    expect(task.events_total).toBe(2);
    expect(task.last_note).toBe('已就绪');
    expect(task.current_block_reason).toBeNull();
    expect(task.depends_on_details).toEqual([
      { task_id: 4, title: '接口设计', status: 'DONE' },
    ]);
    // 事件按 id 升序，便于直接渲染
    expect(task.events?.map((event) => event.id)).toEqual([1, 2]);
    expect(task.events?.[0]?.to_status).toBe('TODO');
  });

  it('normalizes task event pages with cursor and total', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      events: [
        { id: 5, task_id: 8, actor_id: -2, from_status: null, to_status: null, content: '系统推进', created_at: '2026-05-29T13:00:00' },
        { id: 4, task_id: 8, actor_id: 3, from_status: null, to_status: null, content: '评论', created_at: '2026-05-29T12:50:00' },
        { id: 'bad', task_id: 8, actor_id: 3 },
      ],
      count: 3,
      total: 12,
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const page = await getTaskEventsPage(8, { limit: 20, beforeId: 99 });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain('/tasks/8/events.json');
    expect(url).toContain('limit=20');
    expect(url).toContain('before_id=99');
    expect(page.total).toBe(12);
    // 非法 id 的事件被丢弃
    expect(page.events.map((event) => event.id)).toEqual([4, 5]);
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

describe('getAgentDetail containers', () => {
  beforeEach(() => {
    clearGlobalRequestError();
    clearToken();
    vi.restoreAllMocks();
  });

  it('keeps container created_at ISO string and nulls it out when absent or not a string', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 3,
      name: 'coder',
      team_id: 2,
      created_at: '2026-10-31T08:15:00.123456',
      containers: [
        {
          record_id: 11,
          agent_id: 3,
          container_id: 'sha256:abc',
          container_name: 'togo-agent-3',
          image: 'togo-sandbox:latest',
          state: 'running',
          is_entered: true,
          // 后端 model_dump(mode="json") 把 datetime 序列化成 ISO 8601 字符串
          created_at: '2026-10-31T08:15:00.123456',
        },
        {
          // 预记录：容器尚未创建完成，container_id 与 created_at 都可能为空
          record_id: 12,
          agent_id: 3,
          container_id: null,
          container_name: '',
          image: 'togo-sandbox:latest',
          state: 'missing',
          is_entered: false,
          created_at: null,
        },
        {
          // 后端漏字段或给出非字符串时不能把非字符串透给 UI
          record_id: 13,
          agent_id: 3,
          container_id: 'sha256:def',
          container_name: 'togo-agent-3-b',
          image: 'togo-sandbox:latest',
          state: 'exited',
          is_entered: false,
          created_at: 1730000000,
        },
      ],
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const detail = await getAgentDetail(3);
    const containers = detail.containers ?? [];

    expect(containers.map((item) => item.record_id)).toEqual([11, 12, 13]);
    expect(containers[0].created_at).toBe('2026-10-31T08:15:00.123456');
    expect(containers[1].created_at).toBeNull();
    // 文档 §2.1 写的是 int 时间戳，后端实际给 ISO 字符串；非字符串一律收敛为 null，不猜。
    expect(containers[2].created_at).toBeNull();
    // state 直接透传 Docker 原值，不做翻译
    expect(containers.map((item) => item.state)).toEqual(['running', 'missing', 'exited']);
    // container_id 为 null 的预记录不能丢（record_id 是删除入口）
    expect(containers[1].container_id).toBeNull();
  });
});

/**
 * B 项基线：`list_agent_containers` 的**真实链路**输出喂给前端 normalize。
 *
 * 上面那个 describe 用的是手写 payload，验的是「normalize 本身的规则」。
 * 这里换一份真取样（迁移 0017 建表 → 真实 DAL 写入 → list_agent_containers
 * → to_json()），目的是**在阿张改 core.py / tools.py 之前钉住形状**：
 * 他改完只要产物形状变了，这里就红，而不是等联调时才发现前端白屏。
 *
 * fixture 来源与「为什么不用 controller 那个测试的样例」写在
 * fixtures/list_agent_containers.baseline.json 的 _meta 里。
 */
describe('getAgentDetail: list_agent_containers 真实基线', () => {
  beforeEach(() => {
    clearGlobalRequestError();
    clearToken();
    vi.restoreAllMocks();
  });

  it('daemon 不可用时预记录仍在表里、state 为 unknown、created_at 是 ISO 串', async () => {
    const baseline = baselineContainers.A_daemon_unavailable;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 3, name: '小美', team_id: 2, exec_env: 'host', current_container_id: null,
      containers: baseline, container_count: baseline.length,
    }), { status: 200, headers: { 'content-type': 'application/json' } })));

    const detail = await getAgentDetail(3);
    const containers = detail.containers ?? [];

    // 三项联调断言，逐条钉死：
    // ① 预记录（container_id=null）不丢，record_id 是唯一删除入口
    expect(containers.map((item) => item.record_id)).toEqual([1, 2]);
    expect(containers[0].container_id).toBeNull();
    expect(containers[0].container_name).toBe('agent-3-7');
    // ② created_at 是真 ISO 8601 串，不是 int 时间戳，也不被 normalize 吃掉
    expect(containers[0].created_at).toBe('2026-11-01T12:09:12.049245');
    // ③ state 原值透传：daemon 不可用是 unknown（不是伪造成 missing）
    expect(containers.map((item) => item.state)).toEqual(['unknown', 'unknown']);
    // is_entered 只认 container_id 命中当前容器
    expect(containers.map((item) => item.is_entered)).toEqual([false, false]);
  });

  it('daemon 可用时 state 是 Docker 原值（running/exited），is_entered 命中当前容器', async () => {
    const baseline = baselineContainers.B_daemon_available_inspect_sample;
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      id: 3, name: '小美', team_id: 2, exec_env: 'container',
      current_container_id: 'deadbeef1234',
      containers: baseline, container_count: baseline.length,
    }), { status: 200, headers: { 'content-type': 'application/json' } })));

    const detail = await getAgentDetail(3);
    const containers = detail.containers ?? [];

    // state 直接展示 Docker 原值，不做翻译（§8）
    expect(containers.map((item) => item.state)).toEqual(['running', 'exited']);
    // 当前容器是 record 2，只有它 is_entered
    expect(containers.map((item) => item.is_entered)).toEqual([false, true]);
    // 预记录（record 1）的 container_id 仍是 null，且它没被 normalize 过滤掉
    expect(containers[0].container_id).toBeNull();
    expect(containers).toHaveLength(2);
    // 已回填的 container_id 必须原样读出来 —— 没有这条，把 `item.container_id`
    // 写错成别的字段名也能全绿（undefined → null），容器表所有 ID 会显示为空。
    expect(containers[1].container_id).toBe('deadbeef1234');
    // exec_env 走 normalizeAgent 的收敛：非 'container' 一律 host
    expect(detail.exec_env).toBe('container');
    expect(detail.current_container_id).toBe('deadbeef1234');
  });

  it('软删记录不出现在两份基线里（list_active_by_agent 的过滤生效）', () => {
    const all = [
      ...baselineContainers.A_daemon_unavailable,
      ...baselineContainers.B_daemon_available_inspect_sample,
    ];
    expect(all.every((item) => item.record_id !== 3)).toBe(true);
  });
});
