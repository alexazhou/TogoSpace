import { defineConfig, type ProxyOptions } from 'vite';
import vue from '@vitejs/plugin-vue';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const backendTarget = 'http://127.0.0.1:8180';

// 去掉 HTML 中的 crossorigin 属性：Tauri v2 自定义协议下会导致资源加载失败
function removeCrossoriginPlugin() {
  return {
    name: 'remove-crossorigin',
    closeBundle() {
      const distDir = fileURLToPath(new URL('./dist', import.meta.url));
      for (const file of ['index.html', 'splash.html']) {
        const path = resolve(distDir, file);
        try {
          const html = readFileSync(path, 'utf-8');
          writeFileSync(path, html.replace(/ crossorigin/g, ''), 'utf-8');
        } catch (error) {
          console.warn('remove-crossorigin:', error instanceof Error ? error.message : error);
        }
      }
    },
  };
}

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
  plugins: [removeCrossoriginPlugin(), vue()],
  define: {
    // 开启 vue-i18n 的 JIT 消息编译：用 AST 解释执行替代运行时 new Function，
    // 避免被 Tauri 的 CSP（script-src 'self'，无 unsafe-eval）拦截导致页面空白。
    __INTLIFY_JIT_COMPILATION__: 'true',
    __INTLIFY_DROP_MESSAGE_COMPILER__: 'false',
  },
  build: {
    rollupOptions: {
      input: {
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        splash: fileURLToPath(new URL('./splash.html', import.meta.url)),
      },
      output: {
        // 分包目的有两个，都不是「让数字好看」：
        // 1) 消除单 chunk 过大（改造前 main.js 807.70 kB / gzip 256.62 kB，触发 Vite
        //    `>500 kB` 警告）；
        // 2) 第三方依赖与业务代码**长效缓存隔离**：改一行业务代码不该让整包第三方库
        //    的缓存一起失效。
        //
        // 判据按「运行时族」分，不按体积分：`vendor-vue` 必须收齐 `vue` / `@vue/*` /
        // `vue-router` / `vue-i18n` / `@intlify/*` —— vue-i18n 与 @intlify 是
        // `vue` 生态的紧耦合运行时，落进 `vendor-others` 会让「升级 vue 补丁版本」
        // 与「升级其它库」互相拖累缓存。
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined;

          if (
            id.includes('/node_modules/vue/') ||
            id.includes('/node_modules/@vue/') ||
            id.includes('/node_modules/vue-router/') ||
            id.includes('/node_modules/vue-i18n/') ||
            id.includes('/node_modules/@intlify/')
          ) {
            return 'vendor-vue';
          }
          // Markdown 渲染链：`markdown-it` 系 + `highlight.js` 单独成块。
          // 必须把**传递依赖**（`linkify-it` / `mdurl` / `uc.micro` / `punycode.js` /
          // `entities`）也收进来 —— 只匹配直接包名会让这条链被劈成两半，一半留在
          // `vendor-markdown`、一半落进 `vendor-others`，而 `vendor-others` 里同时还
          // 装着 `@tauri-apps/api`：那样「升级 markdown-it」会连带作废 tauri 那部分缓存，
          // 与本节「长效缓存隔离」的目的正相反。
          if (
            id.includes('/node_modules/highlight.js/') ||
            id.includes('/node_modules/markdown-it/') ||
            id.includes('/node_modules/markdown-it-task-lists/') ||
            id.includes('/node_modules/linkify-it/') ||
            id.includes('/node_modules/mdurl/') ||
            id.includes('/node_modules/uc.micro/') ||
            id.includes('/node_modules/punycode.js/') ||
            id.includes('/node_modules/entities/')
          ) {
            return 'vendor-markdown';
          }
          return 'vendor-others';
        },
      },
    },
    // Tauri v2 自定义协议下需要关闭 modulePreload
    modulePreload: false,
    // 注意：**没有**设 `chunkSizeWarningLimit`。阈值是发现问题的量具，不是问题本身；
    // 把上限调到 1000 也能让警告消失，但那样的「消除警告」是自证式的。
  },
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
      '/tasks/': createApiProxy(backendTarget),
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
