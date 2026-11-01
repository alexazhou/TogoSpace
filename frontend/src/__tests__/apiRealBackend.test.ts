/**
 * 真实后端响应 → 前端 normalize 对账。
 *
 * 三份 fixture 来自真跑起来的后端取样，并已按 AgentRuntimeInfo 外壳更新：
 * - `agent_detail_real.json`：`GET /agents/3.json`，DB 字段在 `agent` 内，运行时字段在外层；
 * - `agent_list_real.json`：`GET /agents/list.json?team_id=1`；
 * - `agent_containers_real.json`：`GET /agents/3/containers.json`，含 1 条已回填
 *   container_id 的真实容器 + 1 条 container_id=NULL 的预记录。
 *
 * 与 `api.test.ts` 里那批手写用例的分工：
 * - 手写用例验「normalize 的规则本身」（我会不会把 int 收敛成 null 之类）；
 * - 这里验「真实后端的产物喂进来，规则有没有一条对不上」。
 * 两者互补，不重复：手写用例永远不可能发现「后端实际不发这个字段」。
 *
 * 一旦后端改了字段名/形状，这批会先红——这正是它的价值。
 *
 * 注：fixture 是历史快照，抓取时容器名还是旧规则 `agent-3-1` 这种形态；
 * 前端只把 container_name 当不透明字符串透传，不解析其结构。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { clearGlobalRequestError } from '../appUiState';
import { clearToken } from '../authStore';
import { getAgentDetail, getAgentsByTeamId } from '../api';
import detailReal from './fixtures/agent_detail_real.json';
import listReal from './fixtures/agent_list_real.json';
import containersReal from './fixtures/agent_containers_real.json';

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
}

function stubJson(payload: unknown) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)));
}

/** 详情请求会并发取三个接口：详情、容器列表、失败信息。 */
function stubAgentDetail(detail: unknown, containers: unknown[], errorMessage: string | null = null) {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/containers.json')) {
      return Promise.resolve(jsonResponse(containers));
    }
    if (url.includes('/error_message.json')) {
      return Promise.resolve(jsonResponse({ error_message: errorMessage }));
    }
    return Promise.resolve(jsonResponse(detail));
  }));
}

describe('真实后端响应喂给前端 normalize', () => {
  beforeEach(() => {
    clearGlobalRequestError();
    clearToken();
    vi.restoreAllMocks();
  });

  it('① 预记录 container_id=null 不丢，按 record_id 保留整条', async () => {
    stubAgentDetail(detailReal, containersReal);
    const detail = await getAgentDetail(3);
    const containers = detail.containers ?? [];

    // 真实响应里有 2 条：record_id=1 已回填、record_id=2 是预记录
    expect(containers).toHaveLength(2);
    expect(containers.map((item) => item.record_id)).toEqual([1, 2]);

    const pre = containers[1];
    expect(pre.container_id).toBeNull();
    // 预记录靠 container_name 标识，这条必须留下来（它是唯一删除入口）
    expect(pre.container_name).toBe('agent-3-2');
    // 已回填那条的 ID 要原样读出（不是 undefined、不是空串）
    expect(containers[0].container_id).toBe('01dfe3847f7c');
  });

  it('② created_at 真实形态是 ISO 8601 字符串，被原样保留', async () => {
    stubAgentDetail(detailReal, containersReal);
    const detail = await getAgentDetail(3);
    const containers = detail.containers ?? [];

    for (const item of containers) {
      // 后端 model_dump(mode="json") 的真实产物：字符串、T 分隔、微秒精度
      expect(typeof item.created_at).toBe('string');
      expect(item.created_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/);
    }
    // 逐值对账：不能因为 normalize 而丢失精度
    expect(containers[0].created_at).toBe(containersReal[0].record.created_at);
    expect(containers[1].created_at).toBe(containersReal[1].record.created_at);
  });

  it('③ state 原值透传：真实响应里的 created / missing 不被翻译、不被兜底改写', async () => {
    stubAgentDetail(detailReal, containersReal);
    const detail = await getAgentDetail(3);
    const containers = detail.containers ?? [];

    // Docker 原值 created + 本系统哨兵 missing：两者都必须原样上来
    expect(containers.map((item) => item.state)).toEqual(['created', 'missing']);
  });

  it('④ 详情页 container_count 与 containers.length 相等', async () => {
    stubAgentDetail(detailReal, containersReal);
    const detail = await getAgentDetail(3);

    expect(detail.container_count).toBe(2);
    expect(detail.containers?.length).toBe(detail.container_count);
  });

  it('无容器时详情返回 []（空数组），normalize 后不是 null', async () => {
    stubAgentDetail({ ...detailReal, container_count: 0 }, []);
    const detail = await getAgentDetail(3);

    expect(detail.containers).toEqual([]);
    expect(detail.containers).not.toBeNull();
    expect(detail.container_count).toBe(0);
  });

  it('详情外壳：状态与执行环境从外层读，不要求 agent 内重复字段', async () => {
    stubAgentDetail(detailReal, containersReal);
    const detail = await getAgentDetail(3);

    expect(detail.id).toBe(3);
    expect(detail.status).toBe('idle');
    expect(detail.exec_env).toBe('host');
    expect(detail.current_container_id).toBeNull();
  });

  it('列表接口：三字段经 normalizeAgent 后都在，且 exec_env 收敛正确', async () => {
    stubJson(listReal);
    const agents = await getAgentsByTeamId(1);

    expect(agents.length).toBeGreaterThan(0);
    for (const agent of agents) {
      // normalizeAgent 的契约：exec_env 只可能是这两个值，其余一律收敛成 host；
      // container_count 是 number | null（不是 undefined）
      expect(['host', 'container']).toContain(agent.exec_env);
      expect(typeof agent.container_count).toBe('number');
      // current_container_id 在宿主态必须是 null 而不是 undefined
      expect(agent.current_container_id).toBeNull();
    }

    // 真实场景：所有 Agent 都在宿主（本次没做 enter_container），但李哥有 2 个容器。
    // 这正是最容易被前端写错的组合——「有容器」不等于「在容器里」。
    const li = agents.find((agent) => agent.id === 3);
    expect(li?.exec_env).toBe('host');
    expect(li?.current_container_id).toBeNull();
    expect(li?.container_count).toBe(2);
  });

  it('后端原始响应：运行时字段在外层，DB 字段在内层（缺字段时前端只能靠 undefined 猜）', () => {
    const items = (listReal as { agents: Record<string, unknown>[] }).agents;
    for (const item of items) {
      expect(item).toHaveProperty('status');
      expect(item).toHaveProperty('exec_env_state');
      expect(item).toHaveProperty('container_count');

      const agent = item.agent as Record<string, unknown>;
      expect(agent).toBeTypeOf('object');
      expect(agent).not.toHaveProperty('status');
      expect(agent).not.toHaveProperty('exec_env');
      expect(agent).not.toHaveProperty('containers');
    }
  });
});
