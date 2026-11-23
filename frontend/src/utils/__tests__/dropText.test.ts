import { describe, expect, it } from 'vitest';
import { buildDropText, insertDroppedText } from '../dropText';

describe('buildDropText', () => {
  it('单个文件名原样返回，不带前后换行', () => {
    expect(buildDropText(['report.pdf'])).toBe('report.pdf');
  });

  it('多文件逐行拼接', () => {
    expect(buildDropText(['a.txt', 'b.png'])).toBe('a.txt\nb.png');
  });

  it('逐项 trim 并丢掉空项', () => {
    expect(buildDropText(['  a.txt  ', '', '   ', 'b.png'])).toBe('a.txt\nb.png');
  });

  it('全是空项时返回空串', () => {
    expect(buildDropText(['', '   '])).toBe('');
    expect(buildDropText([])).toBe('');
  });

  it('绝对路径（桌面端形态）不做任何改写', () => {
    expect(buildDropText(['/Users/alexa/Desktop/a b.txt'])).toBe('/Users/alexa/Desktop/a b.txt');
  });
});

describe('insertDroppedText', () => {
  it('空文本 + 光标 0 ⇒ 直接插入，光标落在末尾', () => {
    expect(insertDroppedText('', 0, 0, 'a.txt')).toEqual({ value: 'a.txt', caret: 5 });
  });

  it('插入点紧跟非空白字符 ⇒ 块前补换行', () => {
    expect(insertDroppedText('看他', 2, 2, 'a.txt')).toEqual({ value: '看他\na.txt', caret: 8 });
  });

  it('插入点后紧跟非空白字符 ⇒ 块后补换行', () => {
    expect(insertDroppedText('吗？', 0, 0, 'a.txt')).toEqual({ value: 'a.txt\n吗？', caret: 6 });
  });

  it('已有文本两端都是空白 ⇒ 前后都不补换行', () => {
    expect(insertDroppedText('看 ', 2, 2, 'a.txt')).toEqual({ value: '看 a.txt', caret: 7 });
  });

  it('换行也算空白，不重复补行', () => {
    expect(insertDroppedText('第一行\n', 4, 4, 'a.txt')).toEqual({ value: '第一行\na.txt', caret: 9 });
  });

  it('前后都有内容 ⇒ 前后各补一个换行', () => {
    expect(insertDroppedText('前面后面', 2, 2, 'a.txt')).toEqual({
      value: '前面\na.txt\n后面',
      caret: 9,
    });
  });

  it('有选中区间 ⇒ 替换选中内容', () => {
    expect(insertDroppedText('看[这里]吗', 1, 5, 'a.txt')).toEqual({
      value: '看\na.txt\n吗',
      caret: 8,
    });
  });

  it('多文件块作为整体插入（内部换行保留）', () => {
    expect(insertDroppedText('', 0, 0, 'a.txt\nb.png')).toEqual({ value: 'a.txt\nb.png', caret: 11 });
  });

  it('光标越界会被夹到文本范围内', () => {
    expect(insertDroppedText('abc', 99, 99, 'x')).toEqual({ value: 'abc\nx', caret: 5 });
    expect(insertDroppedText('abc', -5, -5, 'x')).toEqual({ value: 'x\nabc', caret: 2 });
  });

  it('end < start 时按 start 处理（不产生负长度切片）', () => {
    expect(insertDroppedText('abc', 2, 0, 'x')).toEqual({ value: 'ab\nx\nc', caret: 5 });
  });

  it('空块 ⇒ 原样返回', () => {
    expect(insertDroppedText('abc', 1, 1, '')).toEqual({ value: 'abc', caret: 1 });
    expect(insertDroppedText('abc', 1, 1, '   ')).toEqual({ value: 'abc', caret: 1 });
  });

  it('空块 + 越界光标 ⇒ 值不变、光标被夹回范围', () => {
    expect(insertDroppedText('abc', 99, 99, '')).toEqual({ value: 'abc', caret: 3 });
  });
});

describe('insertDroppedText 双插保护（skipIfAlreadyBeforeCaret）', () => {
  it('光标前紧邻已经是这一块 ⇒ 不重复插入', () => {
    expect(insertDroppedText('/tmp/a.txt', 10, 10, '/tmp/a.txt', { skipIfAlreadyBeforeCaret: true })).toEqual({
      value: '/tmp/a.txt',
      caret: 10,
    });
  });

  it('不开启保护 ⇒ 照旧插入（默认行为不受影响）', () => {
    expect(insertDroppedText('/tmp/a.txt', 10, 10, '/tmp/a.txt')).toEqual({
      value: '/tmp/a.txt\n/tmp/a.txt',
      caret: 21,
    });
  });

  it('光标前不是这一块 ⇒ 不误杀（仍然插入）', () => {
    expect(
      insertDroppedText('/tmp/a.txt', 0, 0, '/tmp/a.txt', { skipIfAlreadyBeforeCaret: true }),
    ).toEqual({ value: '/tmp/a.txt\n/tmp/a.txt', caret: 11 });
  });

  it('有选中区间时不走保护（语义是替换）', () => {
    expect(
      insertDroppedText('/tmp/a.txt', 0, 10, '/tmp/a.txt', { skipIfAlreadyBeforeCaret: true }),
    ).toEqual({ value: '/tmp/a.txt', caret: 10 });
  });
});
