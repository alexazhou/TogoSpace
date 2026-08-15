import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { computed, effectScope, nextTick, reactive, ref, type EffectScope } from 'vue';
import { useSettingsRouting } from '../useSettingsRouting';

describe('useSettingsRouting', () => {
  let scope: EffectScope | null = null;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    scope?.stop();
    scope = null;
  });

  function mountRouting(options?: {
    section?: string;
    detailTeamId?: string | undefined;
    thirdPartyService?: string | undefined;
    selectedTeamName?: string | null;
  }) {
    const route = reactive({
      params: {
        teamId: '2',
        section: options?.section ?? 'teams',
      },
      query: {
        detailTeamId: options?.detailTeamId,
        thirdPartyService: options?.thirdPartyService,
      },
    });
    const router = {
      push: vi.fn().mockResolvedValue(undefined),
      replace: vi.fn().mockResolvedValue(undefined),
    };
    const selectedTeamDetail = ref(options?.selectedTeamName
      ? {
        id: 3,
        name: options.selectedTeamName,
        i18n: {},
        working_directory: '',
        config: {},
        max_function_calls: null,
        enabled: true,
        created_at: '',
        updated_at: '',
        members: [],
        rooms: [],
      }
      : null);
    const navItems = computed(() => [
      { id: 'teams' as const, label: 'Teams', note: '' },
      { id: 'roles' as const, label: 'Roles', note: '' },
      { id: 'models' as const, label: 'Models', note: '' },
      { id: 'thirdPartyServices' as const, label: 'Third Party', note: '' },
      { id: 'quickInit' as const, label: 'Quick Init', note: '' },
    ]);
    const openQuickInit = vi.fn();

    scope = effectScope();
    const state = scope.run(() => useSettingsRouting({
      route,
      router,
      teamId: computed(() => Number(route.params.teamId)),
      navItems,
      selectedTeamDetail,
      openQuickInit,
      t: (key: string) => key,
    }));

    if (!state) {
      throw new Error('Failed to mount settings routing');
    }

    return {
      route,
      router,
      selectedTeamDetail,
      openQuickInit,
      state,
    };
  }

  it('redirects invalid route sections to the default settings section', async () => {
    const { router } = mountRouting({ section: 'invalid' });
    await nextTick();

    expect(router.replace).toHaveBeenCalledWith({
      name: 'settings',
      params: { teamId: 2, section: 'teams' },
    });
  });

  it('opens quick init without navigating when selecting quickInit', () => {
    const { openQuickInit, router, state } = mountRouting();

    state.openSection('quickInit');

    expect(openQuickInit).toHaveBeenCalledTimes(1);
    expect(router.push).not.toHaveBeenCalled();
  });

  it('builds team-detail breadcrumbs and supports detail navigation helpers', () => {
    const { router, state } = mountRouting({
      section: 'teams',
      detailTeamId: '9',
      selectedTeamName: 'Alpha',
    });

    expect(state.detailTeamId.value).toBe(9);
    expect(state.breadcrumbItems.value).toEqual([
      { key: 'settings', label: 'settings.title', current: false },
      { key: 'section-teams', label: 'Teams', current: false },
      { key: 'team-detail', label: 'Alpha', current: true },
    ]);

    state.handleBreadcrumbNavigate('section-teams');
    expect(router.push).toHaveBeenCalledWith({
      name: 'settings',
      params: { teamId: 2, section: 'teams' },
    });

    state.goBack();
    expect(router.push).toHaveBeenLastCalledWith({
      name: 'settings',
      params: { teamId: 2, section: 'teams' },
    });
  });

  it('builds third-party service breadcrumbs and returns to the service list', () => {
    const { router, state } = mountRouting({
      section: 'thirdPartyServices',
      thirdPartyService: 'deepseek',
    });

    expect(state.detailThirdPartyService.value).toBe('deepseek');
    expect(state.breadcrumbItems.value).toEqual([
      { key: 'settings', label: 'settings.title', current: false },
      { key: 'section-thirdPartyServices', label: 'Third Party', current: false },
      { key: 'third-party-service', label: 'settings.thirdParty.deepseekTitle', current: true },
    ]);

    state.handleBreadcrumbNavigate('section-thirdPartyServices');
    expect(router.push).toHaveBeenCalledWith({
      name: 'settings',
      params: { teamId: 2, section: 'thirdPartyServices' },
    });
  });

  it('builds Xiaomi MiMo third-party service breadcrumbs', () => {
    const { state } = mountRouting({
      section: 'thirdPartyServices',
      thirdPartyService: 'xiaomi_mimo',
    });

    expect(state.breadcrumbItems.value).toEqual([
      { key: 'settings', label: 'settings.title', current: false },
      { key: 'section-thirdPartyServices', label: 'Third Party', current: false },
      { key: 'third-party-service', label: 'settings.thirdParty.mimoTitle', current: true },
    ]);
  });
});
