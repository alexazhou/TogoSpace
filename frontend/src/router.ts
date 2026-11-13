import { createRouter, createWebHashHistory } from 'vue-router';
// `ConsolePage` 必须保持**静态**导入：根路由 `/` 只是占位，无 activeTeam 时 App.vue
// 会立刻跳到 console，所以它是事实首屏。且 vite.config.ts 为适配 Tauri 自定义协议
// 关闭了 modulePreload（`modulePreload: false`），把首屏组件也改成懒加载不会减少首屏
// 字节，反而会多出「先取 chunk 再执行」的串行请求瀑布。
import ConsolePage from './pages/ConsolePage.vue';

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
      // `SettingsPage`（1153 行，含模型/提供商/第三方服务/团队设置等数十个子面板）
      // 改为按需加载：它不是首屏必经路径，静态导入会让每次冷启动都白传这部分字节。
      component: () => import('./pages/SettingsPage.vue'),
    },
  ],
});

export default router;
