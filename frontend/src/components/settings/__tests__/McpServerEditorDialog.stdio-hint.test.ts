import { describe, expect, it, afterEach, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import McpServerEditorDialog from '../McpServerEditorDialog.vue';
import CustomSelect from '../../ui/CustomSelect.vue';
import InfoTooltip from '../../ui/InfoTooltip.vue';
import i18n from '../../../i18n';

/**
 * #309：MCP 服务器编辑弹窗，stdio 类型的「连接目标」提示必须**完整可读**，
 * 且 vue-i18n 不得再因裸 `@` 报消息编译错误。
 *
 * 背景：`mcpServersTargetHintMcpStdio` 原文含 `npx -y @modelcontextprotocol/server-filesystem /tmp`。
 * `@` 在 vue-i18n 9 里是「链接消息语法」的起始符，裸写会让每次渲染往 console.error 打 3 条
 * `Message compilation error`（修复方式是把它写成字面量插值 `{'@'}`）。
 *
 * 本用例是「改前红、改后绿」的可复跑判据：
 *   - 改前（`@` 裸写）：`Message compilation error × 3` ⇒ 第 3 条断言失败；
 *   - 改后：0 条，且 tooltip 文本与修复前**逐字相同**（用户可见文案零变化）。
 *
 * 为什么必须真挂载而不是只断言 JSON：文案是在组件里经 `useI18n().t()` 取出后渲染到
 * `<InfoTooltip :text="targetHint" />` 的；只有挂载后读 DOM/props 才能同时覆盖
 * 「字段还在不在」和「文案被不被吞」这两件事（任务书最初把症状描述成「字段消失」，
 * 实测字段一直在，详见交付说明）。
 */

/** 用户应当看到的完整提示（= 修复前那句带裸 `@` 的原文，逐字相同）。 */
const EXPECTED_ZH =
  '填启动本地服务的命令，后端以 stdio 方式拉起子进程，如 npx -y @modelcontextprotocol/server-filesystem /tmp';

async function mountDialog() {
  const wrapper = mount(McpServerEditorDialog, { global: { plugins: [i18n] } });
  // 弹窗默认不渲染，先按「新建」打开（组件 expose 了 openCreate）
  (wrapper.vm as unknown as { openCreate: () => void }).openCreate();
  await wrapper.vm.$nextTick();
  return wrapper;
}

/** kind 是第 0 个 CustomSelect，protocol 是第 1 个（仅 kind=mcp 时出现）。 */
async function switchProtocol(
  wrapper: Awaited<ReturnType<typeof mountDialog>>,
  value: string,
): Promise<void> {
  const selects = wrapper.findAllComponents(CustomSelect);
  expect(selects.length).toBe(2);
  await selects[1].vm.$emit('update:modelValue', value);
  await wrapper.vm.$nextTick();
}

describe('#309 McpServerEditorDialog stdio 目标字段与提示', () => {
  afterEach(() => {
    // ModalDialog 用 Teleport 挂到 body，unmount 后仍要清一遍
    document.body.innerHTML = '';
  });

  it('#309 stdio 下「连接目标」字段在、提示完整、vue-i18n 无编译错误', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    const wrapper = await mountDialog();
    await switchProtocol(wrapper, 'stdio');

    // ① 字段在：名称 / 连接目标 / 备注 三个输入框
    const inputs = document.body.querySelectorAll('input.gu-input');
    expect(inputs.length).toBe(3);

    const labels = Array.from(document.body.querySelectorAll('.form-field__label')).map(
      (n) => (n.textContent ?? '').trim(),
    );
    expect(labels.join('|')).toContain('连接目标');

    // ② 提示完整：组件 props 与实际 DOM 文本都要等于原文（含裸 @ 的包名）
    const tooltip = wrapper.findComponent(InfoTooltip);
    expect(tooltip.exists()).toBe(true);
    expect(tooltip.props('text')).toBe(EXPECTED_ZH);
    expect(tooltip.props('text')).toContain('@modelcontextprotocol/server-filesystem');
    const domText = document.body.querySelector('.info-tooltip')?.textContent ?? '';
    expect(domText).toContain('@modelcontextprotocol/server-filesystem');
    // 比「包含」更强的一档：文本必须与修复前那句**逐字相同**，
    // 封死「把 @ 删掉 / 改写文案来消灭编译错误」这条偷懒路径（变异 B 验证过）。
    expect(domText).toBe(EXPECTED_ZH);

    // ③ 渲染过程不得产生 vue-i18n 消息编译错误。
    //    判据按验收要求取「Message compilation error 的条数」，不取 console.error 总数
    //    （后者会被其它无关告警误伤，属于脆判据）。
    //    注意：必须先取计数再 restore —— mockRestore 会清空 calls，先 restore 会恒等于 0（假绿）。
    const compileErrs = errSpy.mock.calls
      .map((c) => String(c[0]))
      .filter((m) => m.includes('Message compilation error'));
    const firstErr = compileErrs.length ? compileErrs[0].split('\n')[0] : '';
    errSpy.mockRestore();
    expect(compileErrs.length, `首条错误：${firstErr}`).toBe(0);

    wrapper.unmount();
  });
});
