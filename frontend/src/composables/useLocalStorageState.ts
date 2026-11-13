/**
 * `localStorage` 的安全读写封装。
 *
 * 存在理由不是「少写几行」，而是把三类容易漏掉的失败收口到一处：
 * 1. **Safari 无痕模式 / 存储被禁用**：`localStorage` 访问本身就可能抛 `SecurityError`；
 * 2. **配额超限**：`setItem` 抛 `QuotaExceededError`（长时间拖拽会高频写入）；
 * 3. **落盘值不可信**：可能是旧版本写的、被手改的、或半截写入的。
 *
 * 约定：`read()` 返回 `null` 表示「没有可用的落盘值」，调用方保持自己的默认值 ——
 * 不要用 `fallback` 强行覆盖，因为各调用点的默认值来源不同（有的是常量、有的是别的 computed）。
 */

export interface LocalStorageStateOptions<T> {
  /**
   * 反序列化 + 校验；返回 `null` 表示「这条落盘值不可用」，等价于没存过。
   *
   * **非字符串类型必须提供**：默认实现是 `raw as T`（字符串原样返回），对 number / object
   * 来说是错的——显式传 `parse` 才能拿到真正的类型与校验。
   */
  parse?: (raw: string) => T | null;
  /** 序列化；默认 `String(value)`。 */
  serialize?: (value: T) => string;
  /** 注入 Storage（测试用）；默认取全局 `localStorage`。 */
  storage?: () => Storage | null;
}

export interface LocalStorageState<T> {
  /** 读：拿不到 / 解析失败 / 不可用一律返回 `null`。 */
  read(): T | null;
  /** 写：任何失败都静默忽略（与原有 try / catch 空处理的语义一致）。 */
  write(value: T): void;
  /** 删：用于「恢复默认」。 */
  clear(): void;
}

function defaultStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    // 访问 localStorage 本身被策略拦截（无痕 / 站点数据禁用）
    return null;
  }
}

export function useLocalStorageState<T>(
  key: string,
  options: LocalStorageStateOptions<T> = {},
): LocalStorageState<T> {
  const parse = options.parse ?? ((raw: string) => raw as unknown as T);
  const serialize = options.serialize ?? ((value: T) => String(value));
  const resolveStorage = options.storage ?? defaultStorage;

  return {
    read(): T | null {
      try {
        const raw = resolveStorage()?.getItem(key);
        if (raw === null || raw === undefined) {
          return null;
        }
        return parse(raw);
      } catch {
        return null;
      }
    },
    write(value: T): void {
      try {
        resolveStorage()?.setItem(key, serialize(value));
      } catch {
        // 配额超限 / 无痕模式：不影响交互，丢掉这次持久化即可
      }
    },
    clear(): void {
      try {
        resolveStorage()?.removeItem(key);
      } catch {
        // 同上
      }
    },
  };
}

/** 数值型落盘值的常用校验器：有限数 + 区间。 */
export function parseFiniteNumberInRange(min: number, max: number) {
  return (raw: string): number | null => {
    const parsed = Number(raw);
    if (!Number.isFinite(parsed) || parsed < min || parsed > max) {
      return null;
    }
    return parsed;
  };
}
