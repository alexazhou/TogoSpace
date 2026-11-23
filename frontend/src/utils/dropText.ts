/**
 * 拖入文件 → 聊天框插入文本的纯逻辑（#339）。
 *
 * 两端能力差异是**物理限制**，不是实现取舍：
 * - 桌面（Tauri）：`onDragDropEvent` 的 `paths` 是本地**绝对路径**；
 * - 浏览器：`DataTransfer.files[i].name` 只有**文件名**，拿不到绝对路径（安全限制）。
 *
 * 所以两端「行为」一致（光标处插入、多文件逐行、行边界干净、不触发跳转），
 * 但「内容强度」不同：桌面给绝对路径，浏览器给文件名。
 */

export type DropInsertResult = {
  /** 插入后的完整文本。 */
  value: string;
  /** 插入后的光标位置（落在插入块之后）。 */
  caret: number;
};

/** 多文件拖入时的行分隔符。 */
export const DROP_ITEM_SEPARATOR = '\n';

/**
 * 把拖入项整理成待插入文本：逐项 `trim`、丢掉空项、每项一行。
 * 单文件时没有前后换行，方便接在已有句子后面。
 */
export function buildDropText(items: readonly string[]): string {
  return items
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
    .join(DROP_ITEM_SEPARATOR);
}

/**
 * 在 `[selectionStart, selectionEnd)` 处插入 `block`，返回新文本与新光标位置。
 *
 * 规则（都有对应单测）：
 * - `block` 为空（或全空白）⇒ 原样返回，光标夹到 `[0, value.length]`；
 * - 选中区间会被替换；
 * - 插入点前一个字符**不是空白** ⇒ 块前补一个换行；插入点后一个字符**不是空白** ⇒ 块后补一个换行。
 *   （避免出现 `看他file:///a.txt吗` 这种粘连，也让多文件逐行时不会粘到上一行末尾）
 */
export function insertDroppedText(
  value: string,
  selectionStart: number,
  selectionEnd: number,
  block: string,
): DropInsertResult {
  const text = block.trim();
  const clamp = (input: number) => Math.min(Math.max(input, 0), value.length);

  if (text.length === 0) {
    return { value, caret: clamp(selectionStart) };
  }

  const start = clamp(selectionStart);
  const end = Math.max(clamp(selectionEnd), start);
  const before = value.slice(0, start);
  const after = value.slice(end);

  const leading = before.length > 0 && !/\s$/.test(before) ? DROP_ITEM_SEPARATOR : '';
  const trailing = after.length > 0 && !/^\s/.test(after) ? DROP_ITEM_SEPARATOR : '';

  return {
    value: `${before}${leading}${text}${trailing}${after}`,
    caret: start + leading.length + text.length + trailing.length,
  };
}
