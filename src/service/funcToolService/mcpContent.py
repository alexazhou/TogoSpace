"""把 MCP 工具结果里的图片、音频和内嵌资源写成临时文件，只把文件路径交给模型。

现象：截图类工具不传 `filePath` 时，旧代码会把近 1MB 的 base64 字符串整段塞进对话，模型读不懂，还占掉大量上下文，也没有任何「这是图片」的标记。
原因：MCP 返回的 `content[]` 有 `text` / `image` / `audio` / `resource_link` / `resource` 五种写法，旧代码只认 `text` 和 `data`，`type` 与 `mimeType` 全部丢掉，所以这里按 `content[].type` 分开处理。
要改这里：任何分支都不许把 base64 或资源正文拼回文本，失败也返回带原因的占位文本，改完跑 `tests/unit/service/funcToolService/test_mcp_multimodal_content.py`；每种类型的处理与文件何时删除见 `docs/design/2026-08-16-agent-multimodal-support/step2_technical.md` §10。"""
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
_BASE64_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\r\n")
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
    if type_name == "resource":
        # 内嵌资源：落盘 + 一行提示（Operator 2026-11-17 23:27 / #300 评论 1846）。
        return _render_embedded_resource(item, tool_name)
    # 未知 type：结构兜底（不丢内容；base64 字段省略，见 `_strip_binary_fields`）。
    return _dump_structure(item)


def _render_embedded_resource(item: Any, tool_name: str) -> str:
    """`content[].type == "resource"`：内容落盘 + 提示，**正文与 base64 都不进上下文**。

    需求变更（#300 单评论 1846，Operator 23:27 原话「对 resource 应该 dump 到本地，
    并且给 agent 注入提示」）取代原文档 §4「不展开 `resource`」；不变量改成
    「不把内容正文塞进上下文」，落地形式就是落盘 + 一行提示。

    带 `text` 的文本型资源同样只落盘、不内联（PM 明确这是他的假设；Operator 若要内联
    另改）。落盘复用本模块的 temp 目录、`mcp-{tool}-{uuid8}.{ext}` 命名与
    mimeType→扩展名映射 —— 与 image/audio 是同一条路，不新造第二套。
    """
    resource = _field(item, "resource") or {}
    mime_type = str(_field(resource, "mimeType") or _field(resource, "mime_type") or "")
    blob = _field(resource, "blob")
    if blob is not None:
        return _save_binary(str(blob), mime_type, tool_name, "资源")
    text = _field(resource, "text")
    if text is not None:
        return _save_text(str(text), mime_type, tool_name, "资源")
    # 协议上不该出现「既无 blob 又无 text」：退回结构兜底，至少保住 uri / 元信息。
    return _dump_structure(item)


def _save_binary(data: str, mime_type: str, tool_name: str, label: str) -> str:
    """base64 解码 → 落盘，返回占位文本。解码失败不回吐 base64。"""
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        logger.warning(
            f"mcp 二进制内容解码失败，已省略: tool={tool_name}, mime={mime_type}, chars={len(data)}"
        )
        return f"[{label}无法解码: {mime_type or '未知类型'}（data 长度 {len(data)} 字符，已省略）]"
    return _persist(raw, mime_type, tool_name, label)


def _save_text(text: str, mime_type: str, tool_name: str, label: str) -> str:
    """文本型内嵌资源：UTF-8 落盘，上下文只留提示（正文不内联）。"""
    return _persist(text.encode("utf-8"), mime_type, tool_name, label)


def _persist(raw: bytes, mime_type: str, tool_name: str, label: str) -> str:
    """统一落盘 + 占位：超限 / 扩展名推导 / magic 校验 / 写入 / 提示都在这一处。

    image、audio、resource 三类走同一条路（共用 temp 目录、命名与 mime 映射），
    避免同一件事出现两套实现与两套口径。
    """
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
    """`resource` / 未知 type 的结构化兜底（base64 字段省略，不灌上下文）。

    序列化失败时**只降级、不回吐原始对象**：`str(item)` 会绕过 `_strip_binary_fields`，
    把未处理的二进制（base64）原样带进上下文，等于 fail-open。这里改为对**已剥离二进制**
    的 `payload` 再用 `default` 序列化一次，无法序列化的叶子只留类型名占位。
    """
    payload = _strip_binary_fields(_jsonable(item))
    try:
        return json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        try:
            return json.dumps(payload, ensure_ascii=False, default=_unsupported_placeholder)
        except (TypeError, ValueError):
            # 二次失败（如循环引用）：内容一律不回吐，只报外层类型。
            return f"<结构化内容无法序列化: {type(item).__name__}>"


def _unsupported_placeholder(value: Any) -> str:
    """`json.dumps` 的 `default` 兜底：**只描述类型，不调用 `str(value)`**。

    理由：`str(value)` 可能带出该对象内部的原始二进制/大段文本，而 `payload` 里
    「键名是 `blob`/`data`」的那类字段才被 `_strip_binary_fields` 处理过，对象内部字段没有。
    宁可少给一个字段的值，也不让这套省略机制被一条异常路径绕过。
    """
    return f"<{type(value).__name__} 无法序列化，已省略>"


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
