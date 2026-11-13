import { describe, expect, it, afterEach } from 'vitest';
import { mount } from '@vue/test-utils';
import TeamPresetExportDialog from '../TeamPresetExportDialog.vue';
import i18n from '../../../i18n';
import type { TeamSummary } from '../../../types';

// 本文件是为 #213「9 个弹窗迁移 ModalDialog」提供的迁移前后行为/结构判据。
// 迁移前该组件自带 `.team-export-overlay` + Teleport；迁移后由 ModalDialog(bare) 提供遮罩。
// 判据只断言「对外可观察行为与视觉参数落点」，不依赖内部实现细节：
//   1) 遮罩自点击 → close；2) 点面板内容 → 不 close；3) 取消按钮 → close；
//   4) 面板样式仍由本组件自己的 class 承载（没被 ModalDialog 的 .ui-modal__dialog 包一层）；
//   5) 遮罩视觉参数（padding/底色/blur/z-index）与迁移前逐项相同。

function teams(): TeamSummary[] {
  return [
    { id: 1, name: 'Alpha', member_count: 2 },
    { id: 2, name: 'Beta', member_count: 3 },
  ] as unknown as TeamSummary[];
}

function queryOverlay(): HTMLElement | null {
  return document.body.querySelector('.ui-modal__overlay');
}

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(TeamPresetExportDialog, {
    props: { open: true, teams: teams(), selectedTeamId: 1, isExporting: false, ...props },
    global: { plugins: [i18n] },
  });
}

describe('TeamPresetExportDialog（迁移 ModalDialog 后）', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('open=false 时不渲染遮罩', () => {
    mountDialog({ open: false });
    expect(queryOverlay()).toBeNull();
  });

  it('渲染遮罩，且面板仍由本组件自己的 .team-export-dialog.panel 承载（没有多一层 .ui-modal__dialog）', () => {
    mountDialog();
    const overlay = queryOverlay();
    expect(overlay).not.toBeNull();
    expect(overlay!.querySelector('.ui-modal__dialog')).toBeNull();
    expect(overlay!.querySelector('.ui-modal__head')).toBeNull();
    const panel = overlay!.firstElementChild as HTMLElement;
    expect(panel.classList.contains('team-export-dialog')).toBe(true);
    expect(panel.classList.contains('panel')).toBe(true);
  });

  it('遮罩视觉参数与迁移前逐项一致（padding 28px / rgba(6,10,16,.52) / blur(8px) / z-index 120）', () => {
    mountDialog();
    const overlay = queryOverlay()!;
    expect(overlay.style.padding).toBe('28px');
    expect(overlay.style.background).toContain('rgba(6, 10, 16, 0.52)');
    expect(overlay.style.backdropFilter).toBe('blur(8px)');
    expect(overlay.style.zIndex).toBe('120');
  });

  it('遮罩自点击 → emit close', async () => {
    const wrapper = mountDialog();
    queryOverlay()!.click();
    expect(wrapper.emitted('close')).toHaveLength(1);
  });

  it('点面板内容 → 不 close', async () => {
    const wrapper = mountDialog();
    await (queryOverlay()!.querySelector('.team-export-dialog') as HTMLElement).click();
    expect(wrapper.emitted('close')).toBeFalsy();
  });

  it('取消按钮 → emit close', async () => {
    const wrapper = mountDialog();
    const cancel = queryOverlay()!.querySelector('.team-export-actions .ghost-button') as HTMLElement;
    await cancel.click();
    expect(wrapper.emitted('close')).toHaveLength(1);
  });

  it('确认按钮 → emit confirm，不改 selectedTeamId 时按钮可用', async () => {
    const wrapper = mountDialog();
    const confirm = queryOverlay()!.querySelector('.team-export-actions .secondary-button') as HTMLElement;
    expect((confirm as HTMLButtonElement).disabled).toBe(false);
    await confirm.click();
    expect(wrapper.emitted('confirm')).toHaveLength(1);
  });

  it('selectedTeamId 为 null 时确认按钮禁用（行为与迁移前一致）', () => {
    mountDialog({ selectedTeamId: null });
    const confirm = queryOverlay()!.querySelector('.team-export-actions .secondary-button') as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
  });
});
