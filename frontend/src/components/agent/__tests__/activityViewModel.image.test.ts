import { describe, expect, it } from 'vitest';
import {
  buildActivityView,
  buildImageMetaText,
  getActivityImage,
  type Translate,
} from '../activity/activityViewModel';
import type { AgentActivity } from '../../../types';

/**
 * #361：read_image 活动的图片数据提取。
 *
 * 数据形状来自后端真实记录（`metadata.tool_result`，只读查
 * `dev_storage_root/data/data.db` 得来），这里按同样的键构造：
 * `{mime_type, base64, format, width, height, size_bytes, file_path}`。
 *
 * 判据重心在**fail-closed**：任何一项不满足都必须是 null（退回文本态），
 * 不能渲染半张图或把 base64 当文本灌进卡片。
 */
const BASE64 = 'iVBORw0KGgoAAAANSUhEUg==';

function act(overrides: Partial<AgentActivity> = {}): AgentActivity {
  return {
    id: 1,
    agent_id: 27,
    team_id: 2,
    activity_type: 'tool_call',
    status: 'succeeded',
    title: '调用工具',
    detail: 'read_image',
    started_at: '2026-11-25 10:00:00.000000',
    duration_ms: 800,
    metadata: {},
    ...overrides,
  } as AgentActivity;
}

function readImageActivity(result: unknown, metadata: Record<string, unknown> = {}): AgentActivity {
  return act({
    metadata: {
      tool_name: 'read_image',
      tool_arguments: { file_path: '/tmp/shots360/old_edge.png' },
      tool_result: result,
      ...metadata,
    },
  });
}

const FULL_RESULT = {
  file_path: '/tmp/shots360/old_edge.png',
  format: 'png',
  width: 171,
  height: 1000,
  mime_type: 'image/png',
  size_bytes: 25474,
  base64: BASE64,
};

describe('#361 getActivityImage 图片数据提取', () => {
  it('完整结果 → 拼出 data URL 与元数据', () => {
    const image = getActivityImage(readImageActivity(FULL_RESULT));
    expect(image).not.toBeNull();
    expect(image?.src).toBe(`data:image/png;base64,${BASE64}`);
    expect(image?.mimeType).toBe('image/png');
    expect(image?.format).toBe('png');
    expect(image?.width).toBe(171);
    expect(image?.height).toBe(1000);
    expect(image?.sizeBytes).toBe(25474);
    expect(image?.filePath).toBe('/tmp/shots360/old_edge.png');
  });

  it('base64 含换行/空白也能拼出合法 data URL（空白被剔除）', () => {
    const image = getActivityImage(readImageActivity({
      ...FULL_RESULT,
      base64: 'iVBORw0KGgo\nAAAANSUhEUg==',
    }));
    expect(image?.src).toBe('data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==');
  });

  it('非 read_image 工具即便结果里有 mime/base64 也不出图', () => {
    const activity = act({
      detail: 'read_file',
      metadata: { tool_name: 'read_file', tool_result: FULL_RESULT },
    });
    expect(getActivityImage(activity)).toBeNull();
  });

  it('read_image 失败结果（无 base64）→ null，交回文本态', () => {
    const activity = readImageActivity({ success: false, message: '文件不存在' });
    expect(getActivityImage(activity)).toBeNull();
  });

  it('mime_type 不是 image/* → null', () => {
    expect(getActivityImage(readImageActivity({ ...FULL_RESULT, mime_type: 'text/plain' }))).toBeNull();
    expect(getActivityImage(readImageActivity({ ...FULL_RESULT, mime_type: undefined }))).toBeNull();
  });

  it('base64 为空串 / 全空白 / 非字符串 → null', () => {
    expect(getActivityImage(readImageActivity({ ...FULL_RESULT, base64: '' }))).toBeNull();
    expect(getActivityImage(readImageActivity({ ...FULL_RESULT, base64: '   \n ' }))).toBeNull();
    expect(getActivityImage(readImageActivity({ ...FULL_RESULT, base64: 123 as unknown as string }))).toBeNull();
  });

  it('tool_result 是数组 → null（不当对象处理）', () => {
    expect(getActivityImage(readImageActivity([FULL_RESULT]))).toBeNull();
  });

  it('width/height/size_bytes 缺失或非正数 → 该字段 null，但图仍可用', () => {
    const image = getActivityImage(readImageActivity({
      ...FULL_RESULT,
      width: 0,
      height: '1000',
      size_bytes: -1,
    }));
    expect(image).not.toBeNull();
    expect(image?.width).toBeNull();
    expect(image?.height).toBeNull();
    expect(image?.sizeBytes).toBeNull();
  });

  it('非 tool_call 活动 → null', () => {
    const activity = act({
      activity_type: 'llm_infer',
      metadata: { tool_name: 'read_image', tool_result: FULL_RESULT },
    });
    expect(getActivityImage(activity)).toBeNull();
  });
});

describe('#361 buildImageMetaText 说明行', () => {
  it('尺寸/格式/大小/文件名都在时按序拼接', () => {
    const image = getActivityImage(readImageActivity(FULL_RESULT));
    expect(buildImageMetaText(image!)).toBe('171×1000 · png · 25 KB · old_edge.png');
  });

  it('缺尺寸与大小 → 只留能显示的部分，不出现 undefined/NaN', () => {
    const image = getActivityImage(readImageActivity({
      mime_type: 'image/jpeg',
      base64: BASE64,
      file_path: '/tmp/a.jpg',
    }));
    const text = buildImageMetaText(image!);
    expect(text).toBe('jpeg · a.jpg');
    expect(text).not.toContain('undefined');
    expect(text).not.toContain('NaN');
  });
});

describe('#361 buildActivityView 接线', () => {
  const t = ((key: string) => key) as unknown as Translate;

  function build(activity: AgentActivity) {
    return buildActivityView({ activity, t, resolveTaskRoomName: () => '' });
  }

  it('read_image 有图 → view.image 有值，且文本预览为空（base64 不进卡片）', () => {
    const view = build(readImageActivity(FULL_RESULT));
    expect(view.image).not.toBeNull();
    expect(view.image?.src.startsWith('data:image/png;base64,')).toBe(true);
    expect(view.toolResult).toBe('');
    expect(view.expandedToolResult).toBe(false);
    // 图片块是实体内容，卡片要按展开态排版（与改动前「有文本结果即展开」一致）
    expect(view.expandedContent).toBe(true);
  });

  it('read_image 无图 → view.image 为 null，错误文本照旧显示', () => {
    const view = build(readImageActivity({ success: false, message: '文件不存在' }));
    expect(view.image).toBeNull();
    expect(view.toolResult).toContain('文件不存在');
    expect(view.expandedToolResult).toBe(true);
  });

  it('其它工具行为不变：结果文本仍在 toolResult 里', () => {
    const activity = act({
      detail: 'read_file',
      metadata: { tool_name: 'read_file', tool_result: { content: 'hello' } },
    });
    expect(build(activity).image).toBeNull();
    expect(build(activity).toolResult).toContain('hello');
  });
});
