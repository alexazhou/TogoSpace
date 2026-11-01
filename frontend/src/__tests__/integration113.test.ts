/**
 * #113 前后端联调对账：真实 HTTP 响应 → 前端 normalize。
 *
 * 这两份 fixture 不是手写的，是 `curl 127.0.0.1:8199` 从真跑起来的后端抓的：
 * - `lg113_detail_real.json`：`GET /teams/1/agents/李哥.json`，该 Agent 库里有
 *   1 条已回填 docker_id 的真实容器 + 1 条 docker_id=NULL 的预记录；
 * - `lg113_list_real.json`：`GET /agents/list.json?team_id=1`。
 *
 * 与 `api.test.ts` 里那批手写用例的分工：
 * - 手写用例验「normalize 的规则本身」（我会不会把 int 收敛成 null 之类）；
 * - 这里验「真实后端的产物喂进来，规则有没有一条对不上」。
 * 两者互补，不重复：手写用例永远不可能发现「后端实际不发这个字段」。
 *
 * 一旦后端改了字段名/形状，这批会先红——这正是它的价值。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { clearGlobalRequestError } from '../appUiState';
import { clearToken } from '../authStore';
import { getAgentDetail, getAgentsByTeamId } from '../api';
import detailReal from './fixtures/lg113_detail_real.json';
import listReal from './fixtures/lg113_list_real.json';

function stubJson(payload: unknown) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  })));
}

describe('#113 联调：真实后端响应喂给前端 normalize', () => {
  beforeEach(() => {
    clearGlobalRequestError();
    clearToken();
    vi.restoreAllMocks();
  });

  it('① 预记录 docker_id=null 不丢，按 record_id 保留整条', async () => {
    stubJson(detailReal);
    const detail = await getAgentDetail(3);
    const containers = detail.containers ?? [];

    // 真实响应里有 2 条：record_id=1 已回填、record_id=2 是预记录
    expect(containers).toHaveLength(2);
    expect(containers.map((item) => item.record_id)).toEqual([1, 2]);

    const pre = containers[1];
    expect(pre.docker_id).toBeNull();
    // 预记录靠 container_name 标识，这条必须留下来（它是唯一删除入口）
    expect(pre.container_name).toBe('agent-3-2');
    // 已回填那条的 ID 要原样读出（不是 undefined、不是空串）
    expect(containers[0].docker_id).toBe('01dfe3847f7c');
  });

  it('② created_at 真实形态是 ISO 8601 字符串，被原样保留', async () => {
    stubJson(detailReal);
    const detail = await getAgentDetail(3);
    const containers = detail.containers ?? [];

    for (const item of containers) {
      // 后端 model_dump(mode="json") 的真实产物：字符串、T 分隔、微秒精度
      expect(typeof item.created_at).toBe('string');
      expect(item.created_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/);
    }
    // 逐值对账：不能因为 normalize 而丢失精度
    expect(containers[0].created_at).toBe((detailReal as { containers: { created_at: string }[] }).containers[0].created_at);
    expect(containers[1].created_at).toBe((detailReal as { containers: { created_at: string }[] }).containers[1].created_at);
  });

  it('③ state 原值透传：真实响应里的 created / missing 不被翻译、不被兜底改写', async () => {
    stubJson(detailReal);
    const detail = await getAgentDetail(3);
    const containers = detail.containers ?? [];

    // Docker 原值 created + 本系统哨兵 missing：两者都必须原样上来
    expect(containers.map((item) => item.state)).toEqual(['created', 'missing']);
  });

  it('④ 详情页 container_count 与 containers.length 相等', async () => {
    stubJson(detailReal);
    const detail = await getAgentDetail(3);

    expect(detail.container_count).toBe(2);
    expect(detail.containers?.length).toBe(detail.container_count);
  });

  it('无容器时详情返回 []（空数组），normalize 后不是 null', async () => {
    // 用列表响应里 container_count=0 的那个 Agent 的详情形状构造
    stubJson({
      ...detailReal,
      containers: [],
      container_count: 0,
    });
    const detail = await getAgentDetail(3);

    expect(detail.containers).toEqual([]);
    expect(detail.containers).not.toBeNull();
    expect(detail.container_count).toBe(0);
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

  it('后端原始响应：三字段逐条存在（缺字段时前端只能靠 undefined 猜）', () => {
    const agents = (listReal as { agents: Record<string, unknown>[] }).agents;
    for (const agent of agents) {
      expect(agent).toHaveProperty('exec_env');
      expect(agent).toHaveProperty('current_container_id');
      expect(agent).toHaveProperty('container_count');
      // 列表接口不该带 containers（详情才有），带上是浪费也不符合契约
      expect(agent).not.toHaveProperty('containers');
    }
  });
});
