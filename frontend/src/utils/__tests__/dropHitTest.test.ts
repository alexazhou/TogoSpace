import { describe, expect, it } from 'vitest';
import { isDropInsideRect } from '../dropHitTest';

/** 一个 200×100 的输入框矩形（CSS 像素）。 */
const RECT = { left: 100, top: 50, width: 200, height: 100 };

describe('isDropInsideRect', () => {
  it('DPR = 1：点在矩形内 ⇒ 命中', () => {
    expect(isDropInsideRect({ x: 150, y: 100 }, RECT, 1)).toBe(true);
  });

  it('DPR = 1：点落在矩形外（右 / 下 / 左 / 上）⇒ 未命中', () => {
    expect(isDropInsideRect({ x: 301, y: 100 }, RECT, 1)).toBe(false);
    expect(isDropInsideRect({ x: 150, y: 151 }, RECT, 1)).toBe(false);
    expect(isDropInsideRect({ x: 99, y: 100 }, RECT, 1)).toBe(false);
    expect(isDropInsideRect({ x: 150, y: 49 }, RECT, 1)).toBe(false);
  });

  it('边界（左上角 / 右下角）算命中', () => {
    expect(isDropInsideRect({ x: 100, y: 50 }, RECT, 1)).toBe(true);
    expect(isDropInsideRect({ x: 300, y: 150 }, RECT, 1)).toBe(true);
  });

  it('DPR = 2：物理像素先除以 DPR 再比较（(400,200) ⇒ css (200,100)）', () => {
    expect(isDropInsideRect({ x: 400, y: 200 }, RECT, 2)).toBe(true);
  });

  it('DPR = 2：同一物理坐标若按 CSS 像素直比会误判，这里必须判未命中', () => {
    // css (200,100) 在矩形内；物理 (400,200) 若被当成 CSS 就会落到 (400,200) 之外
    expect(isDropInsideRect({ x: 620, y: 200 }, RECT, 2)).toBe(false);
    // 物理 (400,200) ⇒ css (200,100) ⇒ 命中（与上一条对照，说明除的是 DPR 而不是别的）
    expect(isDropInsideRect({ x: 400, y: 200 }, RECT, 2)).toBe(true);
  });

  it('DPR = 1.5（缩放屏）：按实际 DPR 换算', () => {
    // css 目标点 (200,100) ⇒ 物理 (300,150)
    expect(isDropInsideRect({ x: 300, y: 150 }, RECT, 1.5)).toBe(true);
    expect(isDropInsideRect({ x: 460, y: 150 }, RECT, 1.5)).toBe(false);
  });

  it('无 position（undefined / null）⇒ fail-closed', () => {
    expect(isDropInsideRect(undefined, RECT, 1)).toBe(false);
    expect(isDropInsideRect(null, RECT, 1)).toBe(false);
  });

  it('position 含非有限数 ⇒ fail-closed', () => {
    expect(isDropInsideRect({ x: Number.NaN, y: 100 }, RECT, 1)).toBe(false);
    expect(isDropInsideRect({ x: 150, y: Number.POSITIVE_INFINITY }, RECT, 1)).toBe(false);
  });

  it('无 rect / rect 尺寸不为正 ⇒ fail-closed（未挂载或不可见）', () => {
    expect(isDropInsideRect({ x: 150, y: 100 }, null, 1)).toBe(false);
    expect(isDropInsideRect({ x: 150, y: 100 }, { ...RECT, width: 0 }, 1)).toBe(false);
    expect(isDropInsideRect({ x: 150, y: 100 }, { ...RECT, height: 0 }, 1)).toBe(false);
  });

  it('DPR 非法（0 / NaN / undefined）⇒ fail-closed', () => {
    expect(isDropInsideRect({ x: 150, y: 100 }, RECT, 0)).toBe(false);
    expect(isDropInsideRect({ x: 150, y: 100 }, RECT, Number.NaN)).toBe(false);
    expect(isDropInsideRect({ x: 150, y: 100 }, RECT, undefined)).toBe(false);
  });

  it('矩形在滚动/偏移后的位置（left/top 为负）也按同一坐标系比较', () => {
    const scrolled = { left: -40, top: -20, width: 200, height: 100 };
    expect(isDropInsideRect({ x: 0, y: 0 }, scrolled, 1)).toBe(true);
    expect(isDropInsideRect({ x: -41, y: 0 }, scrolled, 1)).toBe(false);
  });
});
