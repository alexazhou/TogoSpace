import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import AgentActivityItem from '../AgentActivityItem.vue';
import i18n from '../../../i18n';
import type { AgentActivity } from '../../../types';

// #213 ③ 拆子组件时的 scoped 样式「命中集不变」判据（验收人判据表 S-4 / S-5）。
// 机理：scoped 规则编译成 `.x[data-v-parent]`，元素一旦出现在子组件自己的模板里，
// 父规则就不再命中（插槽内容例外）。本文件用真实 mount + querySelectorAll 计数，
// 断言「拆分后每条原 selector 的命中元素数不变」——只在命中集真的变小才判红。
//
// 用法约定：EXPECTED 里的数字是**拆分前**（单文件版）实测值，拆分后必须逐个相等。

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

const FIXTURES: AgentActivity[] = [
  // bash 结果（含 command/stdout/stderr）
  act({
    id: 2,
    activity_type: 'tool_call',
    status: 'failed',
    detail: 'execute_bash',
    metadata: {
      tool_name: 'execute_bash',
      command: 'ls -la',
      tool_arguments: { command: 'ls -la', description: 'list files' },
      tool_result: { stdout: 'a.txt\n', stderr: 'warn\n', exit_code: 1 },
    },
  }),
  // 收到的消息
  act({
    id: 3,
    activity_type: 'message_received',
    metadata: { messages: [{ sender: 'alice', content: 'hi' }, { sender: 'bob', content: 'yo' }] },
  }),
  // 长回复（触发折叠 + 展开按钮）
  act({
    id: 4,
    activity_type: 'chat_reply',
    detail: Array.from({ length: 8 }, (_, i) => `line ${i}`).join('\n'),
  }),
  // send_chat_msg（内容块 + 折叠按钮）
  act({
    id: 5,
    activity_type: 'tool_call',
    detail: 'send_chat_msg',
    metadata: {
      tool_name: 'send_chat_msg',
      tool_arguments: { room_name: 'dev', msg: Array.from({ length: 8 }, (_, i) => `msg ${i}`).join('\n') },
    },
  }),
  // 普通工具结果
  act({
    id: 6,
    activity_type: 'tool_call',
    detail: 'read_file',
    metadata: { tool_name: 'read_file', tool_arguments: { file_path: '/tmp/a' }, tool_result: 'content' },
  }),
  // 思考内容（reasoning 折叠）
  act({
    id: 7,
    activity_type: 'reasoning',
    detail: Array.from({ length: 6 }, (_, i) => `think ${i}`).join('\n'),
  }),
];

// selector -> 拆分前实测命中数（跨上面全部 fixture 求和）
const EXPECTED: Record<string, number> = {
  '.agent-activity-item__tool-result': 4,
  '.agent-activity-item__tool-result--code': 1,
  '.agent-activity-item__tool-result--stdout': 1,
  '.agent-activity-item__tool-result--stderr': 1,
  '.agent-activity-item__received-message': 2,
  '.agent-activity-item__received-sender': 2,
  '.agent-activity-item__chat-reply': 2,
  '.agent-activity-item__chat-reply--collapsed': 0,
  '.agent-activity-item__chat-reply-toggle': 2,
};

describe('#213 AgentActivityItem 拆分后样式命中集不变', () => {
  for (const [selector, expected] of Object.entries(EXPECTED)) {
    it(`命中集计数不变：${selector} = ${expected}`, () => {
      let total = 0;
      for (const fixture of FIXTURES) {
        const wrapper = mount(AgentActivityItem, {
          props: { activity: fixture },
          global: { plugins: [i18n] },
          attachTo: document.body,
        });
        total += wrapper.element.querySelectorAll(selector).length;
        wrapper.unmount();
      }
      expect(total, `${selector} 命中数变化（拆分导致样式丢失/命中缩小）`).toBe(expected);
    });
  }
});
