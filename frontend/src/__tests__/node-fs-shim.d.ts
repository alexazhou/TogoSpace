/**
 * 极简 node 类型垫片（仅供 src/__tests__ 下的源码扫描用例使用）。
 *
 * app 的 tsconfig `types` 只有 vite/client + vitest/globals，没有 @types/node，
 * 但 `titlebarDragRegionStacking.test.ts` 需要读 `src/**` 源码；
 * vite 的 raw glob 读 `.css` 在 vitest 里恒为空串（CSS 被 stub），只能用 fs。
 * 这里只声明实际用到的 API，保持最小。
 *
 * 若将来项目引入 @types/node，请删除本文件，避免重复声明。
 */
declare module 'node:fs' {
  export function readFileSync(path: string, encoding: 'utf8'): string;
  export function readdirSync(path: string): string[];
  export function statSync(path: string): { isDirectory(): boolean };
}

declare module 'node:path' {
  export function join(...parts: string[]): string;
}

declare const process: { cwd(): string };
