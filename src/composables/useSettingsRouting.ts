import { computed, watch, type ComputedRef, type Ref } from 'vue';
import { DEFAULT_SETTINGS_SECTION, isSettingsRouteSection } from '../components/settings/sections';
import type { SettingsNavItem } from '../components/settings/settingsNavItems';
import type { SettingsBreadcrumbItem } from '../components/settings/types';
import type { TeamDetail } from '../types';

type SettingsRouteLike = {
  params: Record<string, unknown>;
  query: Record<string, unknown>;
};

type SettingsRouterLike = {
  push: (location: {
    name: string;
    params?: Record<string, any>;
    query?: Record<string, any>;
  }) => Promise<unknown>;
  replace: (location: {
    name: string;
    params?: Record<string, any>;
    query?: Record<string, any>;
  }) => Promise<unknown>;
};

type TranslateFn = (key: string, params?: Record<string, string | number>) => string;

type UseSettingsRoutingOptions = {
  route: SettingsRouteLike;
  router: SettingsRouterLike;
  teamId: ComputedRef<number>;
  navItems: ComputedRef<SettingsNavItem[]>;
  selectedTeamDetail: Ref<TeamDetail | null>;
  openQuickInit: () => void;
  t: TranslateFn;
};

export function useSettingsRouting(options: UseSettingsRoutingOptions) {
  const routeSection = computed(() =>
    typeof options.route.params.section === 'string' ? options.route.params.section : '',
  );
  const currentSectionId = computed(() =>
    isSettingsRouteSection(routeSection.value) ? routeSection.value : DEFAULT_SETTINGS_SECTION,
  );
  const currentNavItem = computed(() =>
    options.navItems.value.find((item) => item.id === currentSectionId.value) ?? options.navItems.value[0],
  );
  const detailTeamId = computed(() => {
    const raw = options.route.query.detailTeamId;
    if (typeof raw !== 'string') {
      return null;
    }
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  });
  
  const detailProviderIndex = computed(() => {
    const raw = options.route.query.detailProviderIndex;
    if (typeof raw !== 'string') {
      return null;
    }
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  });

  const detailThirdPartyService = computed(() => {
    const raw = options.route.query.thirdPartyService;
    return typeof raw === 'string' && raw ? raw : null;
  });

  const isTeamDetailView = computed(() => currentSectionId.value === 'teams' && detailTeamId.value !== null);
  const isProviderModelsView = computed(() => currentSectionId.value === 'models' && detailProviderIndex.value !== null);
  const isThirdPartyServiceView = computed(() => currentSectionId.value === 'thirdPartyServices' && detailThirdPartyService.value !== null);
  
  const topbarBackLabel = computed(() => {
    if (isTeamDetailView.value) return options.t('settings.backToTeams');
    if (isThirdPartyServiceView.value) return options.t('settings.thirdParty.backToList');
    return options.t('settings.back');
  });
  
  const breadcrumbItems = computed<SettingsBreadcrumbItem[]>(() => {
    const items: SettingsBreadcrumbItem[] = [
      { key: 'settings', label: options.t('settings.title'), current: false },
      {
        key: `section-${currentNavItem.value.id}`,
        label: currentNavItem.value.label,
        current: detailTeamId.value === null && detailProviderIndex.value === null && detailThirdPartyService.value === null,
      },
    ];

    if (currentSectionId.value === 'teams' && options.selectedTeamDetail.value) {
      items[items.length - 1].current = false;
      items.push({
        key: 'team-detail',
        label: options.selectedTeamDetail.value.name,
        current: true,
      });
    } else if (currentSectionId.value === 'models' && detailProviderIndex.value !== null) {
      items[items.length - 1].current = false;
      items.push({
        key: 'provider-models',
        label: 'Models',
        current: true,
      });
    } else if (
      currentSectionId.value === 'thirdPartyServices'
      && (detailThirdPartyService.value === 'deepseek' || detailThirdPartyService.value === 'xiaomi_mimo')
    ) {
      items[items.length - 1].current = false;
      items.push({
        key: 'third-party-service',
        label: detailThirdPartyService.value === 'deepseek'
          ? options.t('settings.thirdParty.deepseekTitle')
          : options.t('settings.thirdParty.mimoTitle'),
        current: true,
      });
    }

    return items;
  });

  function openSection(sectionId: string): void {
    if (sectionId === 'quickInit') {
      options.openQuickInit();
      return;
    }

    options.router.push({
      name: 'settings',
      params: { teamId: options.teamId.value, section: sectionId },
      query: sectionId === 'teams' && detailTeamId.value ? { detailTeamId: String(detailTeamId.value) } : {},
    }).catch(console.error);
  }

  function openTeamDetail(targetTeamId: number): void {
    options.router.push({
      name: 'settings',
      params: { teamId: options.teamId.value, section: 'teams' },
      query: { detailTeamId: String(targetTeamId) },
    }).catch(console.error);
  }

  function clearTeamDetail(): void {
    options.router.push({
      name: 'settings',
      params: { teamId: options.teamId.value, section: 'teams' },
    }).catch(console.error);
  }
  
  function openProviderModels(providerIndex: number): void {
    options.router.push({
      name: 'settings',
      params: { teamId: options.teamId.value, section: 'models' },
      query: { detailProviderIndex: String(providerIndex) },
    }).catch(console.error);
  }

  function clearProviderModels(): void {
    options.router.push({
      name: 'settings',
      params: { teamId: options.teamId.value, section: 'models' },
    }).catch(console.error);
  }

  function openThirdPartyService(serviceName: string): void {
    options.router.push({
      name: 'settings',
      params: { teamId: options.teamId.value, section: 'thirdPartyServices' },
      query: { thirdPartyService: serviceName },
    }).catch(console.error);
  }

  function clearThirdPartyService(): void {
    options.router.push({
      name: 'settings',
      params: { teamId: options.teamId.value, section: 'thirdPartyServices' },
    }).catch(console.error);
  }

  function handleBreadcrumbNavigate(key: string): void {
    if (key === 'settings') {
      openSection(DEFAULT_SETTINGS_SECTION);
      return;
    }

    if (key === 'team-detail') {
      clearTeamDetail();
      return;
    }
    
    if (key === 'provider-models') {
      clearProviderModels();
      return;
    }

    if (key === 'third-party-service') {
      clearThirdPartyService();
      return;
    }

    if (key.startsWith('section-')) {
      const sectionId = key.slice('section-'.length);
      if (sectionId === 'teams') {
        clearTeamDetail();
        return;
      }
      if (sectionId === 'models') {
        clearProviderModels();
        return;
      }
      if (sectionId === 'thirdPartyServices') {
        clearThirdPartyService();
        return;
      }
      openSection(sectionId);
    }
  }

  function goBack(): void {
    if (isTeamDetailView.value) {
      clearTeamDetail();
      return;
    }
    if (isProviderModelsView.value) {
      clearProviderModels();
      return;
    }
    if (isThirdPartyServiceView.value) {
      clearThirdPartyService();
      return;
    }
    options.router.push({ name: 'console', params: { teamId: options.teamId.value } }).catch(console.error);
  }

  watch(
    () => options.route.params.section,
    (section) => {
      if (typeof section !== 'string' || !isSettingsRouteSection(section)) {
        options.router.replace({
          name: 'settings',
          params: { teamId: options.teamId.value, section: DEFAULT_SETTINGS_SECTION },
        }).catch(console.error);
      }
    },
    { immediate: true },
  );

  return {
    breadcrumbItems,
    currentSectionId,
    detailTeamId,
    detailProviderIndex,
    detailThirdPartyService,
    goBack,
    handleBreadcrumbNavigate,
    isTeamDetailView,
    isProviderModelsView,
    isThirdPartyServiceView,
    openSection,
    openTeamDetail,
    clearTeamDetail,
    openProviderModels,
    clearProviderModels,
    openThirdPartyService,
    clearThirdPartyService,
    topbarBackLabel,
  };
}
