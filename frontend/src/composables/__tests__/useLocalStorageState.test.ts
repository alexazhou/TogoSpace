import { describe, expect, it, vi } from 'vitest';
import { parseFiniteNumberInRange, useLocalStorageState } from '../useLocalStorageState';

/** 最小的 Storage 替身：只实现被测到的三个方法，便于注入而不污染全局。 */
function createMemoryStorage(initial: Record<string, string> = {}): Storage {
  const data = new Map(Object.entries(initial));
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key: string) => (data.has(key) ? (data.get(key) as string) : null),
    key: (index: number) => Array.from(data.keys())[index] ?? null,
    removeItem: (key: string) => {
      data.delete(key);
    },
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
  } as Storage;
}

describe('useLocalStorageState', () => {
  it('写入后能原样读回（默认 String 序列化 + 显式 parse）', () => {
    const storage = createMemoryStorage();
    const state = useLocalStorageState<number>('ratio', {
      storage: () => storage,
      parse: parseFiniteNumberInRange(0.2, 0.8),
    });

    state.write(0.62);

    expect(storage.getItem('ratio')).toBe('0.62');
    expect(state.read()).toBe(0.62);
  });

  it('不给 parse 时按字符串原样返回（字符串型 state 的最短用法）', () => {
    const storage = createMemoryStorage({ theme: 'light' });
    const state = useLocalStorageState<string>('theme', { storage: () => storage });

    expect(state.read()).toBe('light');
  });

  it('没存过时 read 返回 null（让调用方保留自己的默认值）', () => {
    const state = useLocalStorageState<string>('missing', { storage: () => createMemoryStorage() });

    expect(state.read()).toBeNull();
  });

  it('parse 拒绝脏数据时 read 返回 null（旧版本 / 手改值不会污染 state）', () => {
    const storage = createMemoryStorage({ ratio: 'not-a-number' });
    const state = useLocalStorageState<number>('ratio', {
      storage: () => storage,
      parse: parseFiniteNumberInRange(0.2, 0.8),
    });

    expect(state.read()).toBeNull();
  });

  it('setItem 抛错（配额超限 / 无痕模式）时不向外抛，交互不受影响', () => {
    const storage = createMemoryStorage();
    storage.setItem = () => {
      throw new Error('QuotaExceededError');
    };
    const state = useLocalStorageState<string>('theme', { storage: () => storage });

    expect(() => state.write('dark')).not.toThrow();
  });

  it('连 localStorage 访问本身被拦截时 read / write / clear 都静默降级', () => {
    const blocked = () => {
      throw new Error('SecurityError: localStorage is disabled');
    };
    const state = useLocalStorageState<string>('theme', { storage: blocked });

    expect(state.read()).toBeNull();
    expect(() => state.write('dark')).not.toThrow();
    expect(() => state.clear()).not.toThrow();
  });

  it('clear 删除落盘值，之后 read 回到 null', () => {
    const storage = createMemoryStorage({ theme: 'light' });
    const state = useLocalStorageState<string>('theme', { storage: () => storage });

    expect(state.read()).toBe('light');
    state.clear();
    expect(state.read()).toBeNull();
  });
});

describe('parseFiniteNumberInRange', () => {
  it('区间内的有限数通过（含两端）', () => {
    const parse = parseFiniteNumberInRange(0.2, 0.8);

    expect(parse('0.2')).toBe(0.2);
    expect(parse('0.8')).toBe(0.8);
    expect(parse('0.62')).toBe(0.62);
  });

  it('越界、非数字、空串一律拒绝', () => {
    const parse = parseFiniteNumberInRange(0.2, 0.8);

    expect(parse('0.19')).toBeNull();
    expect(parse('0.81')).toBeNull();
    expect(parse('abc')).toBeNull();
    expect(parse('')).toBeNull();
    expect(parse('NaN')).toBeNull();
    expect(parse('Infinity')).toBeNull();
  });
});
