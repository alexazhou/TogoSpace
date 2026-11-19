import { describe, expect, it, afterEach } from 'vitest';
import { nextTick } from 'vue';
import { mount } from '@vue/test-utils';
import ModelEditorDialog from '../ModelEditorDialog.vue';
import i18n from '../../../i18n';
import type { LlmModelConfig } from '../../../types';

// #305 判据：模型编辑器保存时不能丢掉「表单没暴露的字段」。
//
// 背景（既存缺陷，本单顺带修）：`handleSave()` 用固定字段重新拼对象，而
// `ModelsSettingsSection.handleModelSave()` 是 `models[idx] = model` 整体替换。
// 后端 `LlmModelConfig` 有 `enabled` / `temperature` 两个字段编辑器并不暴露，
// 于是「编辑 → 确认」会把 `enabled=false` 抹成默认 `true`（**模型被静默启用**）、
// `temperature` 丢掉。本单新增的 `thinking_default` 属同一类。
// 这组判据钉的是对外可观察行为：save 事件里带出来的对象仍保留这些字段。

type RawModel = LlmModelConfig & Record<string, unknown>;

function makeModel(overrides: Record<string, unknown> = {}): RawModel {
  return {
    name: 'deepseek-flash',
    protocol: 'openai',
    input: ['text', 'image'],
    context_config: {
      context_window_tokens: 524288,
      reserve_output_tokens: null,
      compact_trigger_ratio: null,
      compact_summary_max_tokens: null,
    },
    extra_headers: null,
    extra_params: null,
    ...overrides,
  } as RawModel;
}

function mountDialog() {
  return mount(ModelEditorDialog, { global: { plugins: [i18n] } });
}

function clickConfirm(): void {
  const label = i18n.global.t('common.confirm');
  const buttons = Array.from(document.body.querySelectorAll('button'));
  const confirm = buttons.filter((b) => (b.textContent ?? '').trim() === label).pop();
  if (!confirm) throw new Error(`找不到确认按钮（label=${label}）`);
  confirm.click();
}

async function editAndSave(wrapper: ReturnType<typeof mountDialog>, model: RawModel) {
  (wrapper.vm as unknown as { openEdit: (m: RawModel) => void }).openEdit(model);
  await nextTick();
  clickConfirm();
  await nextTick();
  const events = wrapper.emitted('save');
  expect(events).toBeTruthy();
  return events![events!.length - 1][0] as RawModel;
}

describe('ModelEditorDialog（#305：保存时保留表单未暴露的模型字段）', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('编辑 `enabled=false` 的模型并确认 ⇒ 保存后仍为 false（防「静默启用」）', async () => {
    const wrapper = mountDialog();
    const saved = await editAndSave(wrapper, makeModel({ enabled: false }));
    expect(saved.enabled).toBe(false);
  });

  it('编辑带 `temperature` 的模型并确认 ⇒ 保存后 temperature 仍在', async () => {
    const wrapper = mountDialog();
    const saved = await editAndSave(wrapper, makeModel({ temperature: 0.3 }));
    expect(saved.temperature).toBe(0.3);
  });

  it('编辑带 `thinking_default` 的模型并确认 ⇒ 保存后该字段仍在', async () => {
    const wrapper = mountDialog();
    const saved = await editAndSave(wrapper, makeModel({ thinking_default: true }));
    expect(saved.thinking_default).toBe(true);
  });

  it('表单暴露的字段仍然「表单说了算」（预置值不覆盖用户输入）', async () => {
    // 这条防的是「保全」改法的副作用：如果把编辑前的对象展开在表单字段**之后**，
    // 旧值会把用户刚输入的内容覆盖回去 —— 那是另一种坏法，与丢字段相反。
    const wrapper = mountDialog();
    (wrapper.vm as unknown as { openEdit: (m: RawModel) => void }).openEdit(
      makeModel({ name: 'old-name' }),
    );
    await nextTick();
    const input = document.querySelector('#model-editor-name') as HTMLInputElement;
    input.value = 'renamed-model';
    input.dispatchEvent(new Event('input'));
    await nextTick();
    clickConfirm();
    await nextTick();

    const events = wrapper.emitted('save');
    const saved = events![events!.length - 1][0] as RawModel;
    expect(saved.name).toBe('renamed-model');
  });

  it('新建模型不会带上一次编辑对象的字段（防止串味）', async () => {
    const wrapper = mountDialog();
    (wrapper.vm as unknown as { openEdit: (m: RawModel) => void }).openEdit(
      makeModel({ enabled: false, thinking_default: true, temperature: 0.9 }),
    );
    await nextTick();

    (wrapper.vm as unknown as { openCreate: () => void }).openCreate();
    await nextTick();
    const input = document.querySelector('#model-editor-name') as HTMLInputElement;
    input.value = 'brand-new-model';
    input.dispatchEvent(new Event('input'));
    await nextTick();
    clickConfirm();
    await nextTick();

    const events = wrapper.emitted('save');
    const saved = events![events!.length - 1][0] as RawModel;
    expect(saved.name).toBe('brand-new-model');
    expect('enabled' in saved).toBe(false);
    expect('thinking_default' in saved).toBe(false);
    expect('temperature' in saved).toBe(false);
  });

  it('保留任意未声明的字段（防「点名补几个字段」的条件式补丁）', async () => {
    // 这条判据的存在理由是 #305 验收时被变异戳出来的缺口：①②③ 只钉住三个**已知**字段，
    // 一个「点名补 enabled / temperature / thinking_default」的条件式补丁能让它们全绿——
    // 那种实现一旦上游再加一个字段，同样会丢，但 CI 不会响。
    //
    // 所以这里用一个**既不在表单里、也不在前端类型声明里**的字段（`foo_custom`）来钉
    // 「通用保全」本身：只有「先把编辑前的整个对象展开、再用表单字段覆盖」这种实现能保留它。
    // 反过来说，**不要**把 `foo_custom` 加进 `types/index.ts` 的 `LlmModelConfig`——
    // 那是为了让判据通过而改被测对象，等于把判据变成自证。
    const wrapper = mountDialog();
    const saved = await editAndSave(wrapper, makeModel({ foo_custom: 42 }));
    expect(saved.foo_custom).toBe(42);
  });
});
