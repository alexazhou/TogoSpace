import { describe, expect, it } from 'vitest';
import { buildActivityView, type Translate } from '../activity/activityViewModel';
import type { AgentActivity } from '../../../types';

// #213 ③ S-2② 判据：view-model 对 9 种 Activity 类型的字段映射逐项单测。
// 与 activityDomEquivalence.test.ts 的分工：
//   - 那个判「渲染出来的 DOM 是否一模一样」（黑盒）
//   - 这个判「字段映射本身是否正确」（白盒，DOM 看不出来的中间量也能断言）
// 9 种类型：llm_infer / tool_call / compact / agent_state / reasoning / chat_reply /
//           message_received / task_received / unknown

// 用「原样回显 key」的假 t，避免断言依赖文案；带参数时回显 `key|参数`
const t: Translate = (key, named) => (named ? `${key}|${JSON.stringify(named)}` : key);
const resolveTaskRoomName = (teamId: number, taskRoomId: number) => `room-${teamId}-${taskRoomId}`;

function act(overrides: Partial<AgentActivity>): AgentActivity {
  return {
    id: 1,
    team_id: 1,
    agent_id: 1,
    activity_type: 'unknown',
    status: 'succeeded',
    detail: '',
    started_at: '2026-05-08T00:00:00Z',
    duration_ms: 100,
    error_message: null,
    metadata: {},
    ...overrides,
  } as unknown as AgentActivity;
}

function view(overrides: Partial<AgentActivity>) {
  return buildActivityView({ activity: act(overrides), t, resolveTaskRoomName });
}

describe('#213 ③ view-model：9 种 Activity 类型的字段映射', () => {
  it('llm_infer：只要 llm_infer 才显示 model 芯片，且 title 走 llmInfer 文案', () => {
    const v = view({ activity_type: 'llm_infer', detail: 'think', metadata: { model: 'gpt-x' } });
    expect(v.title).toBe('agent.activityType.llmInfer');
    expect(v.model).toBe('gpt-x');
    expect(v.showModelInLlmInfer).toBe(true);
    expect(v.showModelOutsideLlmInfer).toBe(false);
    expect(v.showToolName).toBe(false);
    expect(v.toolResult).toBe('');
  });

  it('llm_infer 失败 + RETRYING：retryText 带 attempt/max，失败态不出摘要', () => {
    const v = view({
      activity_type: 'llm_infer',
      status: 'failed',
      metadata: { request_state: 'RETRYING', retry_attempt: 2, retry_max_attempts: 3 },
    });
    expect(v.retryText).toBe('agent.retryingProgress|{"attempt":2,"max":3}');
    expect(v.summary).toBe('');
    expect(v.stateSymbol).toBe('×');
  });

  it('tool_call（execute_bash）：命令/输出/退出码 + bash 展开标记', () => {
    const v = view({
      activity_type: 'tool_call',
      detail: 'execute_bash',
      metadata: {
        tool_name: 'execute_bash',
        command: 'ls',
        tool_arguments: { command: 'ls', description: 'list' },
        tool_result: { stdout: 'a\n', stderr: 'b\n', exit_code: 2 },
      },
    });
    expect(v.toolName).toBe('execute_bash');
    expect(v.executeBashResult).toBe(true);
    expect(v.executeBashCommand).toBe('$ ls');
    expect(v.stdout).toBe('a');
    expect(v.stderr).toBe('b');
    expect(v.exitCode).toBe('return_code=2');
    expect(v.expandedContent).toBe(true);
    expect(v.summaryIsCode).toBe(true);
  });

  it('tool_call（send_chat_msg）：消息体展开 + 房间前缀，且不出工具名芯片', () => {
    const v = view({
      activity_type: 'tool_call',
      detail: 'send_chat_msg',
      metadata: { tool_name: 'send_chat_msg', tool_arguments: { room_name: 'dev', msg: 'hi' } },
    });
    expect(v.expandedMessage).toBe(true);
    expect(v.sendChatMsgContent).toBe('hi');
    expect(v.sendMessagePrefix).toBe('agent.sendToRoomPrefix|{"room":"dev"}');
    expect(v.showToolName).toBe(false);
    expect(v.showErrorMessage).toBe(false);
  });

  it('tool_call（通用工具）：出工具名芯片 + 参数 + 结果，且 metadataToolName 芯片不重复显示', () => {
    const v = view({
      activity_type: 'tool_call',
      detail: 'read_file',
      metadata: { tool_name: 'read_file', tool_arguments: { file_path: '/tmp/a' }, tool_result: 'content' },
    });
    expect(v.showToolName).toBe(true);
    // 假 t 原样回显 key ⇒ displayToolName 判定「无翻译」并回退到原始工具名
    expect(v.displayToolName).toBe('read_file');
    expect(v.toolArguments).toBe('/tmp/a');
    expect(v.showToolArguments).toBe(true);
    expect(v.toolResult).toBe('content');
    expect(v.showMetadataToolName).toBe(false); // tool_call 下不再显示 metadataToolName 芯片
  });

  it('compact：title 走 compact 文案；失败时不出摘要', () => {
    const ok = view({ activity_type: 'compact', detail: 'x' });
    expect(ok.title).toBe('agent.activityType.compact');
    expect(ok.summary).toBe('x');
    const failed = view({ activity_type: 'compact', status: 'failed', detail: 'x', error_message: 'bad' });
    expect(failed.summary).toBe('');
    expect(failed.showErrorMessage).toBe(true);
  });

  it('agent_state：ACTIVE/IDLE 走专门文案且不出摘要，其他 detail 保留摘要', () => {
    const active = view({ activity_type: 'agent_state', detail: 'ACTIVE', status: 'started' });
    expect(active.title).toBe('agent.activityType.startActivity');
    expect(active.summary).toBe('');
    expect(active.stateSymbol).toBe('●');

    const idle = view({ activity_type: 'agent_state', detail: 'idle' });
    expect(idle.title).toBe('agent.activityType.stopAction');

    const other = view({ activity_type: 'agent_state', detail: 'PAUSED' });
    expect(other.title).toBe('agent.activityType.agentState');
    expect(other.summary).toBe('PAUSED');
  });

  it('reasoning：正文进 reasoningContent，不进摘要、不出 inlineTitle', () => {
    const v = view({ activity_type: 'reasoning', detail: 'a\nb\nc\nd' });
    expect(v.reasoningContent).toBe('a\nb\nc\nd');
    expect(v.summary).toBe('');
    expect(v.inlineTitle).toBe(false);
  });

  it('chat_reply：正文进 chatReplyContent（Markdown 块），摘要为空', () => {
    const v = view({ activity_type: 'chat_reply', detail: 'hello\nworld' });
    expect(v.chatReplyContent).toBe('hello\nworld');
    expect(v.chatReplyIsLong).toBe(false);
    expect(v.summary).toBe('');
    expect(v.showReceivedMessages).toBe(false);
  });

  it('message_received：消息列表 + 任务房间标签，showReceivedMessages 为真', () => {
    const v = view({
      activity_type: 'message_received',
      metadata: { messages: [{ sender: 'alice', content: 'hi' }], task_room_id: 9 },
    });
    expect(v.receivedMessages).toEqual([{ sender: 'alice', content: 'hi' }]);
    expect(v.showReceivedMessages).toBe(true);
    expect(v.taskRoomLabel).toBe('agent.finishActionRoomLabel|{"room":"room-1-9"}');
  });

  it('message_received 无消息：showReceivedMessages 为假', () => {
    const v = view({ activity_type: 'message_received', metadata: {} });
    expect(v.receivedMessages).toEqual([]);
    expect(v.showReceivedMessages).toBe(false);
    expect(v.taskRoomLabel).toBe('');
  });

  it('task_received：detail 进 taskTitle，摘要为空', () => {
    const v = view({ activity_type: 'task_received', detail: '修复登录问题' });
    expect(v.title).toBe('agent.activityType.taskReceived');
    expect(v.taskTitle).toBe('修复登录问题');
    expect(v.summary).toBe('');
  });

  it('unknown：title 回退 unknown 文案，摘要回显 detail', () => {
    const v = view({ activity_type: 'unknown', detail: 'something odd' });
    expect(v.title).toBe('agent.activityType.unknown');
    expect(v.summary).toBe('something odd');
  });

  it('非 llm_infer 类型也显示 model 芯片（showModelOutsideLlmInfer）', () => {
    const v = view({ activity_type: 'compact', detail: 'x', metadata: { model: 'm1' } });
    expect(v.model).toBe('m1');
    expect(v.showModelOutsideLlmInfer).toBe(true);
    expect(v.showModelInLlmInfer).toBe(false);
  });

  it('displayToolName：有翻译用翻译，无翻译回退原始工具名', () => {
    const translated: Translate = (key, named) =>
      key === 'agent.toolNames.read_file' ? '读取文件' : (named ? `${key}|${JSON.stringify(named)}` : key);
    const base = act({
      activity_type: 'tool_call',
      detail: 'read_file',
      metadata: { tool_name: 'read_file', tool_arguments: { file_path: '/tmp/a' } },
    });
    expect(buildActivityView({ activity: base, t: translated, resolveTaskRoomName }).displayToolName).toBe('读取文件');
    expect(buildActivityView({ activity: base, t, resolveTaskRoomName }).displayToolName).toBe('read_file');
  });

  it('exec_env=container：出容器徽标；宿主态不出', () => {
    const container = view({
      activity_type: 'tool_call',
      detail: 'execute_bash',
      metadata: { tool_name: 'execute_bash', exec_env: 'container', container_name: 'az-box' },
    });
    expect(container.execEnvBadge).toBe('az-box');
    expect(container.execEnvBadgeTitle).toBe('agent.execEnv.container · az-box');

    const host = view({ activity_type: 'tool_call', detail: 'execute_bash', metadata: { tool_name: 'execute_bash' } });
    expect(host.execEnvBadge).toBe('');
    expect(host.execEnvBadgeTitle).toBe('');
  });
});
