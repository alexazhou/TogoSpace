import { createRouter, createWebHashHistory } from 'vue-router';
import ConsolePage from './pages/ConsolePage.vue';
import SettingsPage from './pages/SettingsPage.vue';

const router = createRouter({
  // Tauri 自定义协议下非入口路径刷新不可靠，统一使用 hash 路由
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: { template: '<div class="route-placeholder"></div>' },
    },
    {
      path: '/teams/:teamId/rooms/:roomId?',
      name: 'console',
      component: ConsolePage,
    },
    {
      path: '/teams/:teamId/detail',
      name: 'team-detail',
      redirect: (to) => ({
        name: 'settings',
        params: { teamId: to.params.teamId, section: 'teams' },
        query: { detailTeamId: String(to.params.teamId ?? '') },
      }),
    },
    {
      path: '/teams/:teamId/settings/:section?',
      name: 'settings',
      component: SettingsPage,
    },
  ],
});

export default router;
