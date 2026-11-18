import { describe, expect, it } from 'vitest';
import { createI18n } from 'vue-i18n';

/**
 * #310：i18n 全量语法守卫 —— 所有 locale 的**每一个字符串叶子**都必须能被 vue-i18n
 * 正确编译并原样渲染。
 *
 * 起因（#309）：`mcpServersTargetHintMcpStdio` 里写了裸 `@`（`npx -y @modelcontextprotocol/...`），
 * 被 vue-i18n 当「链接消息语法」解析，每次渲染往 console.error 打 3 条 `Message compilation error`。
 * 同一次排查还发现 `settings.models.extraParamsPlaceholder` 的 JSON 占位示例里 `{}` 被当插值。
 * 这类问题只靠人工逐条肉眼查必然反复出现，所以固化成一条自动用例。
 *
 * 两条判据（缺一不可）：
 *   ① 编译不产生 error；
 *   ② 渲染结果等于该叶子的**期望文本**：把 vue-i18n 的「字面量插值」`{'x'}` 还原成 `x`、
 *      把占位符 `{name}` 换成注入的哨兵值之后，渲染结果必须**完全相等**。
 *      为什么要 ②：「没报错」不等于「文案没变」——`@:someKey`（链接到不存在的 key）、
 *      正文里一个裸 `|`（被当复数分隔符）都能**零错误**地把文字吞掉，只有比对才能发现。
 *
 * 已知语义（不是漏洞，写清以免误判）：
 * - `{'x'}` 是 vue-i18n 的字面量插值，渲染出 `x`，因此「渲染 === 文件里的原始字符串」对这类
 *   消息必然不成立；② 比的是「还原字面量之后」的文本（见 `resolveBranch`）。
 * - 含 `|` 的消息按**复数分支**比对（渲染结果命中任一分支即通过），这是 vue-i18n 的定义；
 *   正文里要出现真正的竖线需写成 `{'|'}`。
 * - 含 `{name}` 的消息会注入哨兵 `⟪name⟫` 再渲染，避免「插值本应被替换」被误判成「文字被吞」。
 *
 * 覆盖范围：`frontend/src/locales/*.json` **自动发现**（不硬编码语言），新增语言自动纳入。
 */

/** 自动发现 locale 文件；新增 `src/locales/xx.json` 会被自动覆盖，无需改本文件。 */
const localeModules = import.meta.glob('../*.json', { eager: true }) as Record<
  string,
  { default: Record<string, unknown> }
>;

type Leaf = { path: string; raw: string };

/** key 段只允许这些字符时才用点号路径；否则走 `["seg"]` 括号语法（当前仓库无此类 key）。 */
const SAFE_SEGMENT = /^[A-Za-z0-9_$-]+$/;

function buildPath(segments: string[]): string {
  return segments
    .map((seg, i) => {
      if (seg === '') return '[""]';
      if (SAFE_SEGMENT.test(seg)) return i === 0 ? seg : `.${seg}`;
      return `["${seg.replace(/"/g, '\\"')}"]`;
    })
    .join('');
}

function collectLeaves(node: unknown, segments: string[] = [], out: Leaf[] = []): Leaf[] {
  if (typeof node === 'string') {
    out.push({ path: buildPath(segments), raw: node });
    return out;
  }
  if (node && typeof node === 'object') {
    for (const [k, v] of Object.entries(node as Record<string, unknown>)) {
      collectLeaves(v, [...segments, k], out);
    }
  }
  return out;
}

const LITERAL_RE = /\{'([^']*)'\}/g;
const PLACEHOLDER_RE = /\{([A-Za-z0-9_]+)\}/g;
/** 非 global 版本（`.test()` 会推进 lastIndex，不能复用全局正则）。 */
const HAS_PLACEHOLDER = /\{[A-Za-z0-9_]+\}/;

const sentinel = (name: string): string => `⟪${name}⟫`;

/** 该叶子里出现过的插值占位符名（先剔除字面量插值，避免把 `{'{'}` 当占位符）。 */
function placeholderNames(raw: string): string[] {
  const withoutLiterals = raw.replace(LITERAL_RE, '');
  return [...new Set(Array.from(withoutLiterals.matchAll(PLACEHOLDER_RE), (m) => m[1]))];
}

/** 按 vue-i18n 语义把一条分支还原成「应当渲染出来的文本」。 */
function resolveBranch(branch: string, params: Record<string, string>): string {
  return branch
    .replace(LITERAL_RE, (_m, inner: string) => inner)
    .replace(PLACEHOLDER_RE, (m, name: string) => (name in params ? params[name] : m));
}

/**
 * 期望渲染结果候选集：含 `|` 的消息按复数分支拆开（字面量里的 `|` 不拆），
 * 每条分支各自还原。渲染命中任一分支即算通过。
 */
function expectedVariants(raw: string, params: Record<string, string>): string[] {
  const branches = splitBranches(raw);
  return (branches ?? [raw]).map((b) => resolveBranch(b, params));
}

/** 按字面量外的 `|` 拆复数分支；无分隔符返回 null。 */
function splitBranches(raw: string): string[] | null {
  const cuts: number[] = [];
  const literalRanges: Array<[number, number]> = [];
  for (const m of raw.matchAll(/\{'[^']*'\}/g)) {
    literalRanges.push([m.index ?? 0, (m.index ?? 0) + m[0].length]);
  }
  const inLiteral = (i: number): boolean => literalRanges.some(([a, b]) => i >= a && i < b);

  for (let i = 0; i < raw.length; i += 1) {
    if (raw[i] === '|' && !inLiteral(i)) cuts.push(i);
  }
  if (!cuts.length) return null;

  const branches: string[] = [];
  let start = 0;
  for (const cut of cuts) {
    branches.push(raw.slice(start, cut));
    start = cut + 1;
  }
  branches.push(raw.slice(start));
  return branches.map((b) => b.trim());
}

type Failure = { key: string; reason: string };

/** 逐叶子编译 + 渲染，返回失败清单（空 = 全通过）。 */
function scanMessages(messages: Record<string, unknown>): { failures: Failure[]; leaves: Leaf[] } {
  const leaves = collectLeaves(messages);
  const i18n = createI18n({
    legacy: false,
    locale: 'guard',
    fallbackLocale: false,
    missingWarn: false,
    fallbackWarn: false,
    // 叶子已在 collectLeaves 里按 unknown 收窄；这里只是让 vue-i18n 的泛型通过，
    // 不影响运行时（消息本身仍是原样传入的对象）。
    messages: { guard: messages as Record<string, string> },
  });

  const failures: Failure[] = [];
  const captured: string[] = [];
  const originalError = console.error;
  console.error = (...args: unknown[]) => {
    captured.push(String(args[0]));
  };

  try {
    for (const leaf of leaves) {
      const names = placeholderNames(leaf.raw);
      const params: Record<string, string> = {};
      for (const n of names) params[n] = sentinel(n);

      captured.length = 0;
      let rendered: string;
      try {
        rendered = i18n.global.t(leaf.path, params) as string;
      } catch (e) {
        failures.push({ key: leaf.path, reason: `渲染抛异常：${(e as Error).message}` });
        continue;
      }
      const compileErrors = captured.filter((m) => m.includes('Message compilation error'));

      if (compileErrors.length > 0) {
        failures.push({
          key: leaf.path,
          reason: `编译错误 ${compileErrors.length} 条，首条：${compileErrors[0].split('\n')[0]}`,
        });
        continue;
      }

      // 裸 `|` 且没有任何插值占位符 ⇒ 几乎不可能是复数，而是正文里的竖线被当复数分隔符吞掉后半句。
      // 真要显示竖线应写 {'|'}（那样 splitBranches 不会在此处拆开）。
      const branches = splitBranches(leaf.raw);
      if (branches && !branches.some((b) => HAS_PLACEHOLDER.test(b))) {
        failures.push({
          key: leaf.path,
          reason:
            '正文含裸 `|` 但没有任何插值占位符：vue-i18n 会把它当复数分隔符，只渲染第一个分支，后面的文字被吞掉' +
            `\n        分支：${JSON.stringify(branches)}` +
            `\n        实际渲染：${JSON.stringify(rendered)}` +
            `\n        文件原文：${JSON.stringify(leaf.raw)}` +
            "\n        如确需显示竖线，请写成 {'|'}",
        });
        continue;
      }

      const variants = expectedVariants(leaf.raw, params);
      if (!variants.includes(rendered)) {
        failures.push({
          key: leaf.path,
          reason:
            '渲染结果与原文不符（文字被 vue-i18n 吞掉或改写）\n' +
            `        期望其一：${JSON.stringify(variants[0])}\n` +
            `        实际渲染：${JSON.stringify(rendered)}\n` +
            `        文件原文：${JSON.stringify(leaf.raw)}`,
        });
      }
    }
  } finally {
    console.error = originalError;
  }

  return { failures, leaves };
}

const localeEntries = Object.entries(localeModules)
  .map(([file, mod]) => ({
    name: file.replace(/^.*\//, '').replace(/\.json$/, ''),
    file: file.replace(/^\.\.\//, 'src/locales/'),
    messages: (mod as { default: Record<string, unknown> }).default,
  }))
  .sort((a, b) => a.name.localeCompare(b.name));

describe('#310 i18n 全量语法守卫（所有 locale × 所有字符串叶子）', () => {
  it('#310 自动发现到的 locale 文件数 ≥ 1（防止 glob 失效导致空转绿）', () => {
    expect(localeEntries.length).toBeGreaterThanOrEqual(1);
  });

  it.each(localeEntries)(
    '#310 $name：每个字符串叶子都能编译且原样渲染',
    ({ file, messages }) => {
      const { failures, leaves } = scanMessages(messages);

      // 防「空转绿」：扫到的叶子数为 0 说明遍历/导入环节坏了，必须报错而不是通过
      expect(leaves.length, `${file} 扫到 0 个字符串叶子，判据未真正执行`).toBeGreaterThan(0);

      const report = failures
        .map((f, i) => `${i + 1}) ${file}\n       key：${f.key}\n       原因：${f.reason}`)
        .join('\n');
      expect(failures, `\n${file} 共 ${leaves.length} 个叶子，${failures.length} 个不合格：\n${report}\n`).toEqual([]);
    },
  );

  it('#310 自检：扫描器必须能识别已知坏样本（否则「全绿」无意义）', () => {
    // 三种「坏」：#309 的裸 @、extraParamsPlaceholder 的裸花括号、零错误但吞文字的链接语法
    const bad = {
      bareAt: '如 npx -y @modelcontextprotocol/server-filesystem /tmp',
      bareBrace: '{\n  "reasoning_effort": "high"\n}',
      silentLinked: '见 @:nope.nokey 一节',
    };
    const { failures, leaves } = scanMessages({ bad });

    expect(leaves.length).toBe(3);
    const failedKeys = failures.map((f) => f.key).sort();
    expect(failedKeys).toEqual(['bad.bareAt', 'bad.bareBrace', 'bad.silentLinked']);

    // 同时确认「好」的写法不被误伤：字面量插值 / 占位符 / 复数分支
    const good = {
      literal: "如 npx -y {'@'}modelcontextprotocol/server-filesystem /tmp",
      placeholders: '{count} 团队 / {seconds}s',
      plural: '{count} more pending message | {count} more pending messages',
    };
    expect(scanMessages({ good }).failures).toEqual([]);
  });
});
