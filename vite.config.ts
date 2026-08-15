import { defineConfig, type ProxyOptions } from 'vite';
import vue from '@vitejs/plugin-vue';

const backendTarget = 'http://127.0.0.1:8180';

function createApiProxy(target: string): ProxyOptions {
  return {
    target,
    configure(proxy) {
      proxy.on('error', (_error, _req, res) => {
        const response = res as {
          headersSent?: boolean;
          writeHead: (statusCode: number, headers: Record<string, string>) => void;
          end: (body: string) => void;
        };

        if (response.headersSent) {
          return;
        }

        response.writeHead(503, {
          'Content-Type': 'application/json; charset=utf-8',
          'X-Proxy-Error': 'backend-unavailable',
        });
        response.end(JSON.stringify({
          error_code: 'BACKEND_UNAVAILABLE',
          error_desc: '开发代理无法连接后端服务，请确认后端已启动',
        }));
      });
    },
  };
}

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: 8181,
    proxy: {
      '/config/frontend.json': createApiProxy(backendTarget),
      '/config/directories.json': createApiProxy(backendTarget),
      '/config/language.json': createApiProxy(backendTarget),
      '/config/llm.json': createApiProxy(backendTarget),
      '/config/provider_types.json': createApiProxy(backendTarget),
      '/config/llm_test.json': createApiProxy(backendTarget),
      '/config/third_party_services': createApiProxy(backendTarget),
      '/config/skills/': createApiProxy(backendTarget),
      '/config/tools/': createApiProxy(backendTarget),
      '/config/quick_init.json': createApiProxy(backendTarget),
      '/system/status.json': createApiProxy(backendTarget),
      '/system/check_update.json': createApiProxy(backendTarget),
      '/system/update_config.json': createApiProxy(backendTarget),
      '/system/schedule/resume.json': createApiProxy(backendTarget),
      '/system/database/backup.json': createApiProxy(backendTarget),
      '/role_templates/list.json': createApiProxy(backendTarget),
      '/role_templates/': createApiProxy(backendTarget),
      '/agents/list.json': createApiProxy(backendTarget),
      '/agents/': createApiProxy(backendTarget),
      '/members/list.json': createApiProxy(backendTarget),
      '/rooms/list.json': createApiProxy(backendTarget),
      '/rooms/': createApiProxy(backendTarget),
      '/teams/list.json': createApiProxy(backendTarget),
      '/teams/create.json': createApiProxy(backendTarget),
      '^/teams/.+\\.json(?:\\?.*)?$': createApiProxy(backendTarget),
      '/ws': {
        target: 'ws://127.0.0.1:8180',
        ws: true,
      },
    },
  },
});
