/**
 * 桌面端拖放落点命中判定（#340）。
 *
 * 背景：Tauri 的 `onDragDropEvent` 是 **webview 级**事件，不带「命中哪个 DOM」的信息，
 * `payload.position` 是**物理像素**（相对 webview 左上角）；而 `getBoundingClientRect()`
 * 是 **CSS 像素**（相对视口）。两者原点一致，只差一个 `devicePixelRatio`。
 *
 * 兜底原则是 **fail-closed**：拿不到 position、rect 尺寸为 0、DPR 非法 ⇒ 一律判「未命中」。
 * 宁可漏插（用户再拖一次），也不能乱插（把路径塞进用户没打算插入的草稿里）。
 */

export type PhysicalPoint = { x: number; y: number };

export type CssRect = {
  left: number;
  top: number;
  width: number;
  height: number;
};

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/**
 * 物理像素坐标是否落在给定 CSS 像素矩形内（含边界）。
 *
 * @param position `payload.position`：物理像素；缺失/非法 ⇒ false
 * @param rect `getBoundingClientRect()` 的结果：CSS 像素；宽或高不为正 ⇒ false
 * @param devicePixelRatio `window.devicePixelRatio`；不为正/非法 ⇒ false
 */
export function isDropInsideRect(
  position: PhysicalPoint | null | undefined,
  rect: CssRect | null | undefined,
  devicePixelRatio: number | null | undefined,
): boolean {
  if (!position || !rect) {
    return false;
  }
  if (!isFiniteNumber(position.x) || !isFiniteNumber(position.y)) {
    return false;
  }
  if (!isFiniteNumber(rect.left) || !isFiniteNumber(rect.top)) {
    return false;
  }
  if (!isFiniteNumber(rect.width) || !isFiniteNumber(rect.height)) {
    return false;
  }
  if (rect.width <= 0 || rect.height <= 0) {
    return false;
  }
  if (!isFiniteNumber(devicePixelRatio) || devicePixelRatio <= 0) {
    return false;
  }

  const cssX = position.x / devicePixelRatio;
  const cssY = position.y / devicePixelRatio;

  return (
    cssX >= rect.left &&
    cssX <= rect.left + rect.width &&
    cssY >= rect.top &&
    cssY <= rect.top + rect.height
  );
}
