import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import AgentActivityItem from '../AgentActivityItem.vue';
import Baseline from '../_golden_AgentActivityItem.vue';
import i18n from '../../../i18n';
import type { AgentActivity } from '../../../types';

// #213 ③ 的 DOM 等价判据：把基线版本（commit 72fd5444 的 AgentActivityItem.vue,
// 单文件 1492 行）与拆分后的版本对同一批 activity 各渲染一次，逐字符比对 innerHTML。
// 这条判据不关心实现怎么拆，只认「渲染出来的 DOM 是否一模一样」——
// 拆子组件只要多包一层、少一个 class、属性顺序变，都会立刻暴露。
//
// 脚本化复现：
//   git show 72fd5444:frontend/src/components/agent/AgentActivityItem.vue \
//     > frontend/src/components/agent/_golden_AgentActivityItem.vue
//   npx vitest run src/components/agent/__tests__/activityDomEquivalence.test.ts

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

const FIXTURES: Array<[string, AgentActivity]> = [
  ['agent_state ACTIVE', act({ activity_type: 'agent_state', detail: 'ACTIVE', status: 'started' })],
  ['agent_state IDLE', act({ activity_type: 'agent_state', detail: 'IDLE' })],
  ['llm_infer 带 model', act({ activity_type: 'llm_infer', detail: 'thinking', metadata: { model: 'gpt-x' } })],
  ['llm_infer 失败+重试', act({
    activity_type: 'llm_infer',
    status: 'failed',
    detail: '',
    metadata: { model: 'gpt-x', request_state: 'RETRYING', retry_attempt: 2, retry_max_attempts: 3 },
  })],
  ['llm_infer 重试历史', act({
    activity_type: 'llm_infer',
    status: 'succeeded',
    metadata: { retry_attempt: 3 },
  })],
  ['reasoning 长内容', act({ activity_type: 'reasoning', detail: Array.from({ length: 8 }, (_, i) => `r${i}`).join('\n') })],
  ['chat_reply 长内容', act({ activity_type: 'chat_reply', detail: Array.from({ length: 8 }, (_, i) => `c${i}`).join('\n') })],
  ['chat_reply 短内容', act({ activity_type: 'chat_reply', detail: 'short' })],
  ['message_received 带消息', act({
    activity_type: 'message_received',
    metadata: { messages: [{ sender: 'alice', content: 'hi' }, { sender: 'bob', content: 'yo' }], task_room_id: 9 },
  })],
  ['message_received 无消息', act({ activity_type: 'message_received', metadata: {} })],
  ['tool_call execute_bash 成功', act({
    activity_type: 'tool_call',
    detail: 'execute_bash',
    metadata: {
      tool_name: 'execute_bash',
      command: 'ls -la',
      tool_arguments: { command: 'ls -la', description: 'list files' },
      tool_result: { stdout: 'a.txt\n', stderr: '', exit_code: 0 },
      exec_env: 'container',
      container_name: 'az-box',
    },
  })],
  ['tool_call execute_bash 失败', act({
    activity_type: 'tool_call',
    status: 'failed',
    detail: 'execute_bash',
    metadata: {
      tool_name: 'execute_bash',
      command: 'false',
      tool_arguments: { command: 'false', description: 'fail' },
      tool_result: { stdout: '', stderr: 'boom\n', exit_code: 1 },
    },
  })],
  ['tool_call send_chat_msg 长内容', act({
    activity_type: 'tool_call',
    detail: 'send_chat_msg',
    metadata: {
      tool_name: 'send_chat_msg',
      tool_arguments: { room_name: 'dev', msg: Array.from({ length: 8 }, (_, i) => `m${i}`).join('\n') },
    },
  })],
  ['tool_call send_chat_msg 失败', act({
    activity_type: 'tool_call',
    status: 'failed',
    detail: 'send_chat_msg',
    metadata: { tool_name: 'send_chat_msg', tool_arguments: { room_name: 'dev', msg: 'x' }, tool_result: 'err' },
  })],
  ['tool_call read_file', act({
    activity_type: 'tool_call',
    detail: 'read_file',
    metadata: { tool_name: 'read_file', tool_arguments: { file_path: '/tmp/a' }, tool_result: 'content' },
  })],
  ['tool_call finish_action', act({
    activity_type: 'tool_call',
    detail: 'finish_action',
    metadata: { tool_name: 'finish_action', task_room_id: 9 },
  })],
  ['tool_call start_chat', act({
    activity_type: 'tool_call',
    detail: 'start_chat',
    metadata: { tool_name: 'start_chat', tool_arguments: { agent_name: 'bob' } },
  })],
  ['tool_call create_task', act({
    activity_type: 'tool_call',
    detail: 'create_task',
    metadata: { tool_name: 'create_task', tool_arguments: { title: 'T' }, tool_result: { title: 'T2' } },
  })],
  ['tool_call 无参数工具', act({
    activity_type: 'tool_call',
    detail: 'get_time',
    metadata: { tool_name: 'get_time', tool_arguments: {} },
  })],
  ['compact 失败', act({ activity_type: 'compact', status: 'failed', detail: 'x', error_message: 'bad' })],
];

function render(component: unknown, activity: AgentActivity): string {
  const wrapper = mount(component as never, {
    props: { activity } as never,
    global: { plugins: [i18n] },
  });
  // 比对前剥掉所有 `data-v-xxxx` 属性，原因有两条、都有据：
  // 1) 两个文件是不同的 SFC，scopeId 必然不同，属实现细节不是结构差异；
  // 2) 拆出的子组件模板里没有 <style scoped>，所以它们渲染的元素**根本不带** data-v 属性。
  //    这正是「父 scoped 规则不再命中子组件元素」的机制本身，也是父侧必须改 :deep() 的原因；
  //    样式是否仍然生效由 activityStyleHitSet.test.ts（DOM 命中集）与构建产物 computed style
  //    探针（真实 Chrome，见交付报告 §③）单独证明，不靠这条判据。
  const html = wrapper.html().replace(/\sdata-v-[0-9a-f]+=""/g, '');
  wrapper.unmount();
  return html;
}

describe('#213 ③ AgentActivityItem 拆分前后 DOM 逐字符等价', () => {
  for (const [label, fixture] of FIXTURES) {
    it(`DOM 完全一致：${label}`, () => {
      const before = render(Baseline, fixture);
      const after = render(AgentActivityItem, fixture);
      expect(after).toBe(before);
    });
  }
});
