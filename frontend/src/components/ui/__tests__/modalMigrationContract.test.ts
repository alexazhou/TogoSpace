import { describe, expect, it, afterEach, vi } from 'vitest';
import { mount, type VueWrapper } from '@vue/test-utils';
import i18n from '../../../i18n';
import ConfirmDialog from '../ConfirmDialog.vue';
import TokenDialog from '../TokenDialog.vue';
import SkillDetailDialog from '../../settings/SkillDetailDialog.vue';
import ClearDataChoiceDialog from '../../settings/ClearDataChoiceDialog.vue';
import TeamPresetExportDialog from '../../settings/TeamPresetExportDialog.vue';
import CreateRoomDialog from '../../console/CreateRoomDialog.vue';
import RoomSettingsDialog from '../../console/RoomSettingsDialog.vue';
import DepartmentEditorDialog from '../../team/DepartmentEditorDialog.vue';
import MemberEditorDialog from '../../team/MemberEditorDialog.vue';

// #213 「9 个弹窗迁移 ModalDialog」的行为矩阵（逐弹窗一行一条，不做聚合结论）。
// 迁移前后被判据咬住的 6 项：
//   1 遮罩自点击是否关闭  2 点面板内容是否不关闭  3 结构里没有多出 .ui-modal__dialog / head
//   4 Esc 是否关闭（迁移前全仓无「全局 Esc 关弹窗」，迁移后也不该新增）
//   5 打开时焦点是否被自动夺走（迁移前无焦点陷阱，迁移后也不该有）
//   6 打开时是否锁 body 滚动（迁移前没有，迁移后也不该有）
// 另加：遮罩视觉参数（padding/底色/blur/z-index）逐弹窗与迁移前一致。

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { teamId: '1' } }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    createTeamRoom: vi.fn(async () => ({})),
    updateTeamRoom: vi.fn(async () => ({})),
  };
});

vi.mock('../../../realtime/runtimeStore', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  return {
    ...actual,
    loadRoleTemplates: vi.fn(async () => []),
    loadTeamAgents: vi.fn(async () => []),
    loadTeamRooms: vi.fn(async () => []),
  };
});

type Case = {
  name: string;
  component: unknown;
  props: Record<string, unknown>;
  padding: string;
  background: string;
  backdropFilter: string;
  zIndex: string;
  /** 遮罩是否具备「自点击关闭」语义（TokenDialog 迁移前就没有） */
  closesOnOverlayClick: boolean;
};

const CASES: Case[] = [
  {
    name: 'ConfirmDialog',
    component: ConfirmDialog,
    props: { open: true, title: 'T', message: 'M' },
    padding: '28px',
    background: 'rgba(6, 10, 16, 0.52)',
    backdropFilter: 'blur(8px)',
    zIndex: '120',
    closesOnOverlayClick: true,
  },
  {
    name: 'TokenDialog',
    component: TokenDialog,
    props: {},
    padding: '28px',
    background: 'rgba(6, 10, 16, 0.52)',
    backdropFilter: 'blur(8px)',
    zIndex: '120',
    // 迁移前：token-overlay 没有 @click.self，且组件无 close 事件
    closesOnOverlayClick: false,
  },
  {
    name: 'SkillDetailDialog',
    component: SkillDetailDialog,
    props: { open: true, skill: null },
    padding: '28px',
    background: 'rgba(6, 10, 16, 0.52)',
    backdropFilter: 'blur(8px)',
    zIndex: '120',
    closesOnOverlayClick: true,
  },
  {
    name: 'ClearDataChoiceDialog',
    component: ClearDataChoiceDialog,
    props: { open: true, teamName: 'T', members: [], deptTree: null },
    padding: '28px',
    background: 'rgba(6, 10, 16, 0.52)',
    backdropFilter: 'blur(8px)',
    zIndex: '120',
    closesOnOverlayClick: true,
  },
  {
    name: 'TeamPresetExportDialog',
    component: TeamPresetExportDialog,
    props: { open: true, teams: [], selectedTeamId: null, isExporting: false },
    padding: '28px',
    background: 'rgba(6, 10, 16, 0.52)',
    backdropFilter: 'blur(8px)',
    zIndex: '120',
    closesOnOverlayClick: true,
  },
  {
    name: 'CreateRoomDialog',
    component: CreateRoomDialog,
    props: { open: true },
    padding: '28px',
    background: 'rgba(6, 10, 16, 0.52)',
    backdropFilter: 'blur(8px)',
    zIndex: '60',
    closesOnOverlayClick: true,
  },
  {
    name: 'RoomSettingsDialog',
    component: RoomSettingsDialog,
    props: { open: true, room: null },
    padding: '24px',
    background: 'rgba(6, 10, 16, 0.42)',
    backdropFilter: 'blur(6px)',
    zIndex: '60',
    closesOnOverlayClick: true,
  },
  {
    name: 'DepartmentEditorDialog',
    component: DepartmentEditorDialog,
    props: { open: true, memberName: 'm', departmentName: 'd', departmentResponsibility: 'r' },
    padding: '28px',
    background: 'rgba(6, 10, 16, 0.44)',
    backdropFilter: 'blur(8px)',
    zIndex: '50',
    closesOnOverlayClick: true,
  },
  {
    name: 'MemberEditorDialog',
    component: MemberEditorDialog,
    props: {
      open: true,
      editable: true,
      agentId: null,
      memberName: '',
      status: '',
      employeeNumber: '',
      memberModel: '',
      keyword: '',
      selectedTemplateId: null,
      selectedTemplateName: '',
      currentTemplateModel: '',
      currentTemplateSoul: '',
      modelOptions: [],
      driver: '',
      driverOptions: [],
      templateOptions: [],
    },
    padding: '28px',
    background: 'rgba(6, 10, 16, 0.58)',
    backdropFilter: 'blur(8px)',
    zIndex: '50',
    closesOnOverlayClick: true,
  },
];

function queryOverlay(): HTMLElement | null {
  return document.body.querySelector('.ui-modal__overlay');
}

function mountCase(c: Case): VueWrapper {
  return mount(c.component as never, {
    props: c.props as never,
    global: { plugins: [i18n] },
    attachTo: document.body,
  });
}

describe('#213 弹窗迁移 ModalDialog：逐弹窗行为/结构/视觉参数', () => {
  afterEach(() => {
    document.body.innerHTML = '';
    document.body.style.overflow = '';
  });

  for (const c of CASES) {
    it(`${c.name}：遮罩由 ModalDialog 提供，且不多渲染 dialog 包裹层/head`, () => {
      mountCase(c);
      const overlay = queryOverlay();
      expect(overlay, `${c.name} 未渲染 .ui-modal__overlay`).not.toBeNull();
      expect(overlay!.querySelector('.ui-modal__dialog'), `${c.name} 多出 .ui-modal__dialog`).toBeNull();
      expect(overlay!.querySelector('.ui-modal__head'), `${c.name} 多出 .ui-modal__head`).toBeNull();
    });

    it(`${c.name}：遮罩视觉参数与迁移前一致（padding/底色/blur/z-index）`, () => {
      mountCase(c);
      const ov = queryOverlay()!;
      expect(ov.style.padding, `${c.name} padding`).toBe(c.padding);
      expect(ov.style.background, `${c.name} background`).toContain(c.background);
      expect(ov.style.backdropFilter, `${c.name} backdropFilter`).toBe(c.backdropFilter);
      expect(ov.style.zIndex, `${c.name} zIndex`).toBe(c.zIndex);
    });

    it(`${c.name}：点面板内容不关闭`, async () => {
      const wrapper = mountCase(c);
      const panel = queryOverlay()!.firstElementChild as HTMLElement;
      await panel.click();
      expect(wrapper.emitted('close'), `${c.name} 点内容却触发了 close`).toBeFalsy();
    });

    if (c.closesOnOverlayClick) {
      it(`${c.name}：遮罩自点击关闭`, async () => {
        const wrapper = mountCase(c);
        queryOverlay()!.click();
        expect(wrapper.emitted('close'), `${c.name} 遮罩点击未触发 close`).toHaveLength(1);
      });
    } else {
      it(`${c.name}：遮罩自点击不关闭（迁移前即无此行为，不得新增）`, async () => {
        const wrapper = mountCase(c);
        queryOverlay()!.click();
        expect(wrapper.emitted('close')).toBeFalsy();
        expect(queryOverlay(), `${c.name} 遮罩点击后弹窗消失，属新增行为`).not.toBeNull();
      });
    }

    it(`${c.name}：Esc 不关闭（迁移前后都不支持，不得新增）`, () => {
      const wrapper = mountCase(c);
      for (const key of ['Escape', 'Esc']) {
        document.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
        window.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
      }
      expect(wrapper.emitted('close'), `${c.name} 新增了 Esc 关闭行为`).toBeFalsy();
    });

    it(`${c.name}：打开时焦点不被夺走、body 不锁滚动（迁移前均无）`, () => {
      mountCase(c);
      expect(document.activeElement, `${c.name} 打开后焦点被移走`).toBe(document.body);
      expect(document.body.style.overflow, `${c.name} 打开后锁了 body 滚动`).toBe('');
    });
  }
});
