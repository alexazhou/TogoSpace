/**
 * #319 守护：顶部拖动带必须位于任何全屏遮罩之上。
 *
 * 背景：macOS 走覆盖式标题栏（Tauri `TitleBarStyle::Overlay` + `hidden_title`，
 * `tauri/src-tauri/src/lib.rs`），系统标题栏不可拖，窗口拖动能力**完全**由
 * `App.vue` 里那一条 `data-tauri-drag-region` 承担（全仓唯一一处）。
 * Tauri 的拖动判定（tauri 2.x `src/window/scripts/drag.js`）是 document 级
 * `mousedown` + `e.composedPath()`：命中路径上有拖动带就拖，**与 `pointer-events`
 * 无关，只取决于命中层级**。因此只要有一个 `position: fixed; inset: 0` 的遮罩
 * z-index 高过拖动带，顶部 28px 就再没有可命中的拖动目标，窗口从此拖不动
 * —— 这就是 #319 的现象（改前实测：7 个全屏遮罩 z-index 40～80 全部压在 z-index 5 之上）。
 *
 * 判据（源级静态断言：happy-dom 没有排版引擎，做不了真实 hit-test）：
 * 1. `src/**\/*.{vue,css}` 中所有 `position: fixed; inset: 0` 的规则（未声明 z-index 视为 0），
 *    除豁免清单外，z-index 必须**严格小于**拖动带的 z-index；
 * 2. 豁免清单里的规则必须**仍然**高于拖动带——否则说明豁免理由已失效，该删掉豁免；
 * 3. 拖动带自身的声明（position:fixed / top:0 / 覆盖整宽 / 高度 token）与
 *    `data-tauri-drag-region` 属性必须仍在 App.vue 里，且全仓只有这一处拖动带。
 *
 * 豁免依据（不是遮罩，且必须压在最上层）：
 * - `WindowResizeHandles.vue` 的 `.window-resize-handles`：容器 `pointer-events: none`，
 *   只有四边 4px 的子元素可交互，Windows 自绘窗口的缩放命中最优先。
 * （`WindowControls.vue` 是 `fixed; top:0; right:0` 而非 `inset: 0`，不在本扫描范围；
 *   它同样高于拖动带，保证右上角窗口按钮优先命中，别把它拉进扫描。）
 *
 * 已知边界：`CustomSelect` / `ModelSelect`（z-index 9999）、`HoverTooltip`（99999）
 * 这类**瞬时**浮层可以高于拖动带——它们只在下拉 / 悬停期间出现并跟随触发元素，不长期
 * 占据顶部。若将来出现长期占据顶部又要点击的浮层，应把它收敛到拖动带之下；本用例会先把它拦下。
 * 另：遮罩若把自己的可交互控件画进顶部 28px 拖动带内，那些控件会被拖动带压住——
 * 现有遮罩都是 `place-items: center` + 内边距 ≥16px，卡片内容不进带内。
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

type SourceFile = { file: string; raw: string };
type OverlayRule = { file: string; selector: string; zIndex: number; hasExplicitZ: boolean };

/** 豁免：不在遮罩语义内、且必须压在最上层的规则（键 = 文件名 + 选择器）。 */
const STACKING_EXEMPTIONS: Record<string, string> = {
  'components/layout/WindowResizeHandles.vue .window-resize-handles':
    '窗口边缘缩放手柄（容器 pointer-events:none，仅四边 4px 生效），Windows 自绘窗口必须最优先命中',
};

/** vitest 的 root 在 frontend/，但也兼容从仓库根跑（cwd/frontend/src）。 */
function resolveSrcDir(): string {
  for (const candidate of [join(process.cwd(), 'src'), join(process.cwd(), 'frontend', 'src')]) {
    try {
      if (!statSync(join(candidate, 'App.vue')).isDirectory()) return candidate;
    } catch {
      /* 换下一个候选 */
    }
  }
  throw new Error('找不到 frontend/src 目录：请在 frontend/ 下运行 vitest');
}

const SRC_DIR = resolveSrcDir();

function listCssSources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry.startsWith('.')) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      listCssSources(full, out);
    } else if (full.endsWith('.vue') || full.endsWith('.css')) {
      out.push(full);
    }
  }
  return out;
}

function relativeToSrc(file: string): string {
  return file.slice(SRC_DIR.length).replace(/^[/\\]/, '').replace(/\\/g, '/');
}

function sourceFiles(): SourceFile[] {
  return listCssSources(SRC_DIR).map((file) => ({ file, raw: readFileSync(file, 'utf8') }));
}

function styleTextOf({ file, raw }: SourceFile): string {
  if (file.endsWith('.css')) return raw;
  return [...raw.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((match) => match[1]).join('\n');
}

function parseCssRules(css: string): Array<{ selector: string; body: string }> {
  // 先去掉注释：否则选择器前那段说明注释会被算进选择器文本里。
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '');
  return [...stripped.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({
    selector: match[1].trim(),
    body: match[2],
  }));
}

function readAppSource(): string {
  const app = sourceFiles().find((item) => item.file.endsWith('/App.vue'));
  if (!app) throw new Error('没扫到 App.vue');
  return app.raw;
}

function readDragRegionRule(): { body: string; zIndex: number | null } {
  const app = sourceFiles().find((item) => item.file.endsWith('/App.vue'));
  if (!app) throw new Error('没扫到 App.vue');
  const rule = parseCssRules(styleTextOf(app)).find((item) => item.selector === '.titlebar-drag-region');
  if (!rule) throw new Error('App.vue 里找不到 .titlebar-drag-region 规则');
  const zIndex = rule.body.match(/z-index:\s*(-?\d+)/);
  return { body: rule.body, zIndex: zIndex ? Number(zIndex[1]) : null };
}

function readFullScreenOverlays(): OverlayRule[] {
  const overlays: OverlayRule[] = [];
  for (const source of sourceFiles()) {
    for (const { selector, body } of parseCssRules(styleTextOf(source))) {
      if (!/position:\s*fixed/.test(body)) continue;
      if (!/inset:\s*0(\s*;|\s*$)/.test(body)) continue;
      const matched = body.match(/z-index:\s*(-?\d+)/);
      for (const single of selector.split(',').map((part) => part.trim()).filter(Boolean)) {
        overlays.push({
          file: relativeToSrc(source.file),
          selector: single,
          zIndex: matched ? Number(matched[1]) : 0,
          hasExplicitZ: Boolean(matched),
        });
      }
    }
  }
  return overlays;
}

function format(rule: OverlayRule): string {
  return `${rule.file} ${rule.selector} (z-index: ${rule.hasExplicitZ ? rule.zIndex : '未声明→0'})`;
}

describe('#319 顶部拖动带层级', () => {
  it('拖动带声明完整：fixed + top:0 + 覆盖整宽 + 高度 token', () => {
    const { body } = readDragRegionRule();
    expect(body).toMatch(/position:\s*fixed/);
    expect(body).toMatch(/top:\s*0/);
    expect(body).toMatch(/left:\s*0/);
    expect(body).toMatch(/right:\s*0/);
    expect(body).toMatch(/height:\s*var\(--macos-titlebar-height,\s*28px\)/);
  });

  it('模板里保留 data-tauri-drag-region，且全仓只有这一处拖动带', () => {
    const vueFiles = sourceFiles().filter((item) => item.file.endsWith('.vue'));
    expect(vueFiles.length).toBeGreaterThan(20);
    const dragRegionFiles = vueFiles
      .filter((item) => item.raw.includes('data-tauri-drag-region'))
      .map((item) => relativeToSrc(item.file));
    expect(dragRegionFiles).toEqual(['App.vue']);
    expect(readAppSource()).toMatch(/class="titlebar-drag-region"[\s\S]{0,240}?data-tauri-drag-region/);
  });

  it('扫描到的全屏遮罩数量合理（防空转绿）', () => {
    expect(readFullScreenOverlays().length).toBeGreaterThanOrEqual(5);
  });

  it('已知的全屏遮罩都被扫到（解析器失效要报错，而不是默默通过）', () => {
    const selectors = readFullScreenOverlays().map((rule) => `${rule.file} ${rule.selector}`);
    for (const expected of [
      'components/agent/AgentActivityDialogShell.vue .agent-detail-overlay',
      'components/agent/AgentTaskDetailModal.vue .task-detail-overlay',
      'components/chat/ChatMembersDrawer.vue .chat-members-modal',
      'components/layout/QuickInitModal.vue .quick-init-overlay',
      'components/settings/ThirdPartyServicesSection.vue .test-dialog-overlay',
      'components/team/TeamMembersCard.vue .member-panel--fullscreen',
      'components/ui/ModalDialog.vue .ui-modal__overlay',
    ]) {
      expect(selectors).toContain(expected);
    }
  });

  it('任何全屏遮罩的 z-index 都低于拖动带（否则顶部拖不动）', () => {
    const dragZIndex = readDragRegionRule().zIndex;
    expect(dragZIndex).not.toBeNull();
    const offenders = readFullScreenOverlays()
      .filter((rule) => !(`${rule.file} ${rule.selector}` in STACKING_EXEMPTIONS))
      .filter((rule) => rule.zIndex >= (dragZIndex as number));
    expect(offenders.map(format)).toEqual([]);
  });

  it('豁免清单仍然高于拖动带（含失效豁免的提示）', () => {
    const dragZIndex = readDragRegionRule().zIndex as number;
    const overlays = readFullScreenOverlays();
    for (const [key, reason] of Object.entries(STACKING_EXEMPTIONS)) {
      const rule = overlays.find((item) => `${item.file} ${item.selector}` === key);
      expect(rule, `豁免项 ${key} 已不存在，请删除豁免`).toBeTruthy();
      expect(rule!.zIndex, `豁免项 ${key} 不再高于拖动带，请重新确认：${reason}`).toBeGreaterThan(
        dragZIndex,
      );
    }
  });
});
