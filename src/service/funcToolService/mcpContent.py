"""MCP 工具结果里二进制内容（image / audio）的落盘与文本占位。

需求来源：`need_fix_3.md`《MCP 工具结果多模态内容通用处理方案》§3 方案 A。

## 问题

MCP 协议里 `CallToolResult.content[]` 是**多态数组**：

| type | 结构 |
|---|---|
| `text` | `{type, text}` |
| `image` / `audio` | `{type, data(base64), mimeType}` |
| `resource_link` | `{type, uri, name, mimeType?}` |
| `resource` | `{type, resource: {...}}` |

改造前 `mcpConsole._content_text()` 只做「有 `text` 取 text，否则取 `data` 拼进去」，
`type` 与 `mimeType` **整段丢弃** ⇒ 带截图产出的工具（如 `take_screenshot` 不传
`filePath`）会把近 1MB 裸 base64 灌进 Agent 上下文：LLM 不可读、纯占上下文，
且没有任何「这是图片」的标记。

## 做法

按**协议字段 `content[].type` 分发**，不对任何具体 server 做特判 —— 一次实现，
所有 MCP server（文件类 / 图像类 / 音频类）自动受益：

- `text` → 原样拼文本（现行为不变）
- `image` / `audio` → base64 解码 → 写 `tempfile.gettempdir()` → 返回
  `[图片已保存: <绝对路径> (size, mimeType)]` 占位，Agent 用现有 `read_image` 即可看图
- `resource_link` → 保留 URI（改造前这类段**整段丢失**，因为它既无 `text` 也无 `data`）
- `resource` / 未知 type → JSON 结构兜底，其中 base64 字段省略为占位

## 红线：任何分支都不把 base64 拼回文本

解码失败、超过大小上限、落盘失败 ⇒ 都返回带原因的占位文本。失败时回吐原始 base64
会把「零 base64」的收益全部作废（这正是本单要修的问题）。同理，`resource` 兜底里
的 `blob` / 超长 base64 外观字符串也替换为 `<base64 已省略: N 字符>` ——
若照字面「JSON 序列化」，内嵌资源的 base64 会原样进上下文。

## 生命周期

文件写 `tempfile.gettempdir()`（per-user TMPDIR），由 OS 常规清理回收，本模块**不主动
删除** —— 与 server 侧 `saveTemporaryFile` 的行为一致；且 Agent 可能在其后的回合里
才去 `read_image`，提前删会打断链路。
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: mimeType ⇒ 扩展名。只收常见且能确定 magic bytes 的类型；未命中的一律 `.bin`
#: 并把原始 mimeType 写进占位文本（不丢信息，也不猜）。
MIME_EXTENSIONS: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/svg+xml": ".svg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
    "application/pdf": ".pdf",
}

#: 单文件落盘上限（解码后字节数）。超过则**不落盘**，避免 temp 被刷爆。
MAX_BINARY_BYTES = 20 * 1024 * 1024

#: `content[].type` ⇒ 占位文本里的中文标签。
_TYPE_LABELS = {"image": "图片", "audio": "音频"}

#: 文件名里允许出现的字符（工具名来自远端 server，必须清洗后才能进路径）。
_SAFE_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
_BASE64_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+=\r\n")
#: 「看起来像 base64」的长度门槛：低于它更可能是普通文本，不做省略（避免误伤）。
_BASE64_MIN_CHARS = 512


def render_content_text(content: Iterable[Any], tool_name: str = "mcp") -> str:
    """把 `CallToolResult.content` 渲染成给模型看的文本（多模态段落盘 + 路径占位）。

    Args:
        content: `content[]` 迭代项，元素可以是 MCP SDK 的 pydantic 对象，也可以是
            等价的 dict（测试与 fake server 用）。
        tool_name: 产出这些内容的工具名，只用于落盘文件命名（`mcp-{tool}-{uuid8}.{ext}`），
            便于从 temp 目录溯源。

    Returns:
        各段渲染结果以换行连接。空 `content` 返回空串（与改造前一致）。
    """
    parts: list[str] = []
    for item in content or []:
        rendered = _render_item(item, tool_name)
        if rendered is not None:
            parts.append(rendered)
    return "\n".join(parts)


def _field(item: Any, name: str, default: Any = None) -> Any:
    """统一的字段访问：pydantic 对象走属性，dict 走键。"""
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _render_item(item: Any, tool_name: str) -> Optional[str]:
    """单段渲染。返回 None 表示该段无需输出（如 `text` 为 None）。"""
    type_name = str(_field(item, "type") or "").strip().lower()
    if type_name == "text":
        text = _field(item, "text")
        return None if text is None else str(text)
    if type_name in ("image", "audio"):
        label = _TYPE_LABELS[type_name]
        # 规范字段是 `mimeType`；兼容 `mime_type` 与 `mimeType` 两种写法（fake server / 上游差异）。
        mime_type = str(_field(item, "mimeType") or _field(item, "mime_type") or "")
        data = _field(item, "data")
        if data is None:
            return f"[{label}内容缺失: {mime_type or '未知类型'}]"
        return _save_binary(str(data), mime_type, tool_name, label)
    if type_name == "resource_link":
        uri = str(_field(item, "uri") or "")
        name = str(_field(item, "name") or "")
        mime_type = str(_field(item, "mimeType") or "")
        details = "、".join(part for part in (name, mime_type) if part)
        suffix = f"（{details}）" if details else ""
        return f"[资源链接: {uri}{suffix}]"
    # `resource`（内嵌资源）与未知 type：结构兜底。
    # 注意：改造前这些段是**丢掉的**（既无 text 也无 data），而文档 §3 把它记为
    # 「JSON 序列化兜底」——按文档字面序列化会把内嵌 `blob` 的 base64 灌进上下文，
    # 所以这里序列化的同时省略 base64 字段（见 `_strip_binary_fields`）。
    return _dump_structure(item)


def _save_binary(data: str, mime_type: str, tool_name: str, label: str) -> str:
    """解码 → 校验 → 落盘，返回占位文本。任何失败都不回吐 base64。"""
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        logger.warning(
            f"mcp 二进制内容解码失败，已省略: tool={tool_name}, mime={mime_type}, chars={len(data)}"
        )
        return f"[{label}无法解码: {mime_type or '未知类型'}（data 长度 {len(data)} 字符，已省略）]"

    if len(raw) > MAX_BINARY_BYTES:
        return (
            f"[{label}过大: {_human_size(len(raw))}, {mime_type or '未知类型'}，"
            f"未落盘（上限 {_human_size(MAX_BINARY_BYTES)}）]"
        )

    extension = MIME_EXTENSIONS.get(mime_type.strip().lower(), ".bin")
    mismatch = ""
    if not _magic_ok(extension, raw):
        # mimeType 撒谎（或类型没登记）：不阻断，只降级扩展名并在占位里说明。
        mismatch = f"，声明的 {mime_type} 与文件头不符，已按 .bin 保存"
        extension = ".bin"

    path = _write_temp(raw, tool_name, extension)
    if path is None:
        return f"[{label}保存失败: {mime_type or '未知类型'}（{_human_size(len(raw))}）]"
    return f"[{label}已保存: {path} ({_human_size(len(raw))}, {mime_type or '未知类型'}{mismatch})]"


def _write_temp(raw: bytes, tool_name: str, extension: str) -> Optional[str]:
    """写 temp 文件，返回绝对路径；失败返回 None（调用方只报原因，不回吐内容）。"""
    base = _sanitize_tool_name(tool_name)
    tmpdir = tempfile.gettempdir()
    for _ in range(3):
        candidate = Path(tmpdir) / f"mcp-{base}-{uuid.uuid4().hex[:8]}{extension}"
        try:
            # "xb"：独占创建，避免并发同名覆盖他人文件。
            with open(candidate, "xb") as handle:
                handle.write(raw)
        except FileExistsError:
            continue
        except OSError as exc:
            logger.warning(f"mcp 二进制内容落盘失败: path={candidate}, error={exc}")
            return None
        return str(candidate)
    logger.warning(f"mcp 二进制内容落盘失败：同名候选连续冲突 tool={tool_name}")
    return None


def _sanitize_tool_name(name: str) -> str:
    """工具名进文件名前清洗（远端值不可信，避免路径穿越与非法字符）。"""
    cleaned = "".join(ch if ch in _SAFE_NAME_CHARS else "-" for ch in (name or ""))
    cleaned = cleaned.strip("-.")
    return cleaned[:60] or "tool"


def _magic_ok(extension: str, raw: bytes) -> bool:
    """文件头与扩展名是否一致。未登记的扩展名（`.bin`）不校验，返回 True。"""
    if extension == ".png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == ".jpg":
        return raw.startswith(b"\xff\xd8\xff")
    if extension == ".gif":
        return raw.startswith(b"GIF8")
    if extension == ".webp":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    if extension == ".bmp":
        return raw.startswith(b"BM")
    if extension == ".wav":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    if extension == ".mp3":
        return raw.startswith(b"ID3") or raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    if extension == ".ogg":
        return raw.startswith(b"OggS")
    if extension == ".webm":
        return raw.startswith(b"\x1a\x45\xdf\xa3")
    if extension == ".pdf":
        return raw.startswith(b"%PDF")
    if extension == ".svg":
        head = raw[:200].lstrip()
        return head.startswith(b"<?xml") or head.startswith(b"<svg")
    return True


def _dump_structure(item: Any) -> str:
    """`resource` / 未知 type 的结构化兜底（base64 字段省略，不灌上下文）。"""
    payload = _strip_binary_fields(_jsonable(item))
    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(item)


def _jsonable(value: Any) -> Any:
    """SDK 类型对象 ⇒ 可 JSON 序列化的结构（pydantic v2 `model_dump`）。"""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:  # 非 pydantic 对象的同名方法
            return dump()
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(val) for val in value]
    return value


def _strip_binary_fields(value: Any) -> Any:
    """递归把 base64 内容字段替换成占位。

    两类判定：
    - `blob`：协议里 `BlobResourceContents.blob` **一定是** base64，无长度门槛；
    - 其它键（含 `data`）：只在「长度 ≥ `_BASE64_MIN_CHARS` 且字符集像 base64」时替换，
      避免把普通长文本（或短的 `data` 字段）误判成二进制。
    """
    if isinstance(value, dict):
        stripped: dict[str, Any] = {}
        for key, val in value.items():
            is_blob = key == "blob" and isinstance(val, str) and val
            is_binary_data = (
                key == "data" and isinstance(val, str) and _looks_like_base64(val)
            )
            if is_blob or is_binary_data:
                stripped[key] = f"<base64 已省略: {len(val)} 字符>"
            else:
                stripped[key] = _strip_binary_fields(val)
        return stripped
    if isinstance(value, list):
        return [_strip_binary_fields(item) for item in value]
    return value


def _looks_like_base64(text: str) -> bool:
    """长度 ≥ `_BASE64_MIN_CHARS` 且字符集落在 base64 内才算，避免误伤普通长文本。"""
    if len(text) < _BASE64_MIN_CHARS:
        return False
    sample = text[:_BASE64_MIN_CHARS]
    return bool(sample) and set(sample) <= _BASE64_CHARS


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"
