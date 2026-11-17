"""`mcpContent` 单元判据：按 `content[].type` 分发 + **任何分支都不回吐 base64**。

需求来源 `need_fix_3.md` §3 方案 A / §6 验收 1–3。这一层只测渲染函数本身（不碰连接与
DB），端到端由 `tests/integration/test_funcToolService/test_mcp_multimodal_real_stdio.py`
用真 stdio 子进程覆盖。

反判据（本文件的核心）：
- `_assert_no_base64()` 断言返回文本里**没有任何 100+ 字符的 base64 连续段**，且不含
  PNG magic 的 base64 前缀 `iVBORw0KGgo` —— 只看「有路径」是不够的：改造前的实现
  同样可能拼出含路径的文本，真正要钉的是「base64 不再进上下文」。
- 解码失败 / 超限 / 落盘失败三条失败路径各自断言「不回吐原始内容」，因为失败时回吐
  base64 会让整条收益归零。
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import struct
import tempfile
import zlib
from pathlib import Path

import pytest

from service.funcToolService import mcpContent

#: 导入时先记下真实 TMPDIR（autouse fixture 会把 `tempfile.gettempdir` 换成 tmp_path）。
_REAL_TMPDIR = tempfile.gettempdir()

#: 100+ 字符的 base64 连续段（正常占位文本里最长的是 32 位 uuid hex，不会命中）。
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{100,}={0,2}")


def _make_png(width: int = 2, height: int = 2) -> bytes:
    """构造一个**真 PNG**（zlib 压缩，magic/CRC 都对），用于「扩展名与格式一致」判据。"""
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def _chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )


#: 最小 JPEG（SOI + APP0/JFIF + 少量段 + EOI），`file` 能识别为 JPEG。
_MIN_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300"
    + "ff" * 64
    + "ffc2000b080001000101011100ffc40014000100000000000000000000000000000009"
    + "ffda0008010100003f00d2cf20ffd9"
)

#: 最小 WAV（RIFF....WAVE）。
_MIN_WAV = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + b"\x00" * 36


@pytest.fixture(autouse=True)
def _isolated_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """落盘目录指向 tmp_path：断言路径可追溯、且不污染宿主机 TMPDIR。"""
    monkeypatch.setattr(mcpContent.tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def _assert_no_base64(text: str) -> None:
    assert "iVBORw0KGgo" not in text, f"PNG base64 前缀仍在文本里: {text[:120]}"
    match = _BASE64_RUN.search(text)
    assert match is None, f"文本里仍有 base64 连续段（{len(match.group(0))} 字符）: {text[:120]}"


def _image_item(raw: bytes, mime_type: str = "image/png") -> dict:
    return {
        "type": "image",
        "data": base64.b64encode(raw).decode("ascii"),
        "mimeType": mime_type,
    }


def test_text_segments_unchanged():
    """`text` 段照旧拼接（改造前行为，回归）。"""
    out = mcpContent.render_content_text(
        [{"type": "text", "text": "第一行"}, {"type": "text", "text": "第二行"}]
    )
    assert out == "第一行\n第二行"


def test_empty_content_returns_empty():
    assert mcpContent.render_content_text([]) == ""
    assert mcpContent.render_content_text(None) == ""


def test_image_saved_to_temp_with_path_and_no_base64():
    """PNG ⇒ 落盘 `.png`、占位含绝对路径 + 大小 + mimeType、文本零 base64。"""
    png = _make_png()
    out = mcpContent.render_content_text(
        [{"type": "text", "text": "Took a screenshot."}, _image_item(png)],
        tool_name="take_screenshot",
    )
    _assert_no_base64(out)
    assert out.startswith("Took a screenshot.")
    assert "[图片已保存: " in out
    assert "image/png" in out

    match = re.search(r"\[图片已保存: ([^ ]+) \(([^,]+), image/png\)\]", out)
    assert match, out
    saved = Path(match.group(1))
    assert saved.is_absolute()
    assert saved.parent.name  # 落在 temp 根下（fixture 指向 tmp_path）
    assert saved.suffix == ".png"
    assert saved.name.startswith("mcp-take_screenshot-")
    assert saved.read_bytes() == png, "落盘内容必须与解码后字节逐字节一致"
    assert match.group(2) == f"{len(png)} B", "占位里的大小必须是真实字节数"


def test_audio_saved_with_extension_from_mime():
    out = mcpContent.render_content_text(
        [
            {
                "type": "audio",
                "data": base64.b64encode(_MIN_WAV).decode(),
                "mimeType": "audio/wav",
            }
        ],
        tool_name="say",
    )
    _assert_no_base64(out)
    assert "[音频已保存: " in out
    assert ".wav" in out


def test_jpeg_mime_gives_jpg_extension():
    out = mcpContent.render_content_text(
        [_image_item(_MIN_JPEG, "image/jpeg")], tool_name="take_screenshot"
    )
    _assert_no_base64(out)
    assert ".jpg" in out, out
    assert "image/jpeg" in out


def test_mime_lie_falls_back_to_bin():
    """声明 `image/png` 但字节是 JPEG ⇒ 降级 `.bin` 并说明（防 mimeType 撒谎）。"""
    out = mcpContent.render_content_text(
        [_image_item(_MIN_JPEG, "image/png")], tool_name="shot"
    )
    _assert_no_base64(out)
    assert ".bin" in out, out
    assert "与文件头不符" in out


def test_unknown_mime_uses_bin_and_keeps_declared_value():
    out = mcpContent.render_content_text(
        [_image_item(b"\x00\x01\x02\x03", "application/x-weird")], tool_name="blob"
    )
    _assert_no_base64(out)
    assert ".bin" in out
    assert "application/x-weird" in out, "未知 mimeType 必须原值保留在占位里"


def test_oversized_not_saved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """超过上限 ⇒ **不落盘** + `[图片过大]`，且不回吐内容。"""
    monkeypatch.setattr(mcpContent, "MAX_BINARY_BYTES", 8)
    out = mcpContent.render_content_text([_image_item(_make_png())], tool_name="shot")
    _assert_no_base64(out)
    assert "过大" in out and "未落盘" in out
    assert list(tmp_path.glob("mcp-*")) == [], "超限不得落盘"


def test_invalid_base64_omitted_without_echo():
    """非法 base64 ⇒ `[图片无法解码]`，且**原始 data 一个字符都不出现在输出里**。"""
    bad = "!!!not-base64!!!" * 20
    out = mcpContent.render_content_text(
        [{"type": "image", "data": bad, "mimeType": "image/png"}], tool_name="shot"
    )
    assert "无法解码" in out
    assert "not-base64" not in out, "失败路径不得回吐原始内容"
    assert str(len(bad)) in out, "至少要给出长度，便于判断规模"


def test_missing_data_reports_without_traceback():
    out = mcpContent.render_content_text(
        [{"type": "image", "mimeType": "image/png"}], tool_name="shot"
    )
    assert out == "[图片内容缺失: image/png]"


def test_resource_link_keeps_uri():
    """`resource_link` 改造前是**整段丢失**（既无 text 也无 data），现在保留 URI。"""
    out = mcpContent.render_content_text(
        [
            {
                "type": "resource_link",
                "uri": "file:///tmp/report.pdf",
                "name": "report.pdf",
                "mimeType": "application/pdf",
            }
        ]
    )
    assert "file:///tmp/report.pdf" in out
    assert "report.pdf" in out


def test_embedded_resource_blob_omitted():
    """`resource` 兜底：结构保留，但内嵌 `blob`（base64）必须省略。"""
    blob = base64.b64encode(_make_png(4, 4)).decode()
    out = mcpContent.render_content_text(
        [
            {
                "type": "resource",
                "resource": {
                    "uri": "shot://1",
                    "mimeType": "image/png",
                    "blob": blob,
                },
            }
        ]
    )
    _assert_no_base64(out)
    assert "shot://1" in out
    assert "<base64 已省略" in out
    payload = json.loads(out)
    assert payload["type"] == "resource"
    assert payload["resource"]["mimeType"] == "image/png"


def test_unknown_type_json_fallback():
    out = mcpContent.render_content_text([{"type": "future_kind", "payload": {"a": 1}}])
    payload = json.loads(out)
    assert payload["type"] == "future_kind"
    assert payload["payload"] == {"a": 1}


def test_tool_name_path_traversal_sanitized(tmp_path: Path):
    """远端工具名不可信：不得穿越出 temp 目录。"""
    out = mcpContent.render_content_text(
        [_image_item(_make_png())], tool_name="../../etc/passwd"
    )
    match = re.search(r"\[图片已保存: ([^ ]+) ", out)
    assert match, out
    saved = Path(match.group(1))
    assert saved.parent == tmp_path, f"落盘必须留在 temp 根下，实际 {saved}"
    assert ".." not in saved.name


def test_pydantic_like_object_field_access():
    """SDK 返回的是 pydantic 对象（属性访问），不能只支持 dict。"""

    class _Item:
        type = "text"
        text = "obj-text"

    assert mcpContent.render_content_text([_Item()]) == "obj-text"


def test_human_size_boundaries():
    assert mcpContent._human_size(512) == "512 B"
    assert mcpContent._human_size(2048) == "2.0 KB"
    assert mcpContent._human_size(3 * 1024 * 1024) == "3.0 MB"


def test_realistic_screenshot_context_increment(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
):
    """真实量级：接近截图的 PNG（~480KB）⇒ 上下文增量必须 <200 字符。

    为什么要单独一条：小图标（73 B）的 base64 只有 100 字符，改前的实现也能让
    「<200 字符」这条断言通过 —— 那样它就**没有鉴别力**。这里用固定随机种子的
    400×400 真 PNG（zlib 基本不压缩，尺寸接近文档实测的 600KB PNG），改前会把
    ~640K 字符的 base64 灌进上下文，`len(out) < 200` 必然红；改后是几十字符的占位。
    """
    import random

    case_dir = Path(tempfile.mkdtemp(prefix="mcpcontent-case-", dir=_REAL_TMPDIR))
    monkeypatch.setattr(mcpContent.tempfile, "gettempdir", lambda: str(case_dir))
    request.addfinalizer(lambda: shutil.rmtree(case_dir, ignore_errors=True))

    rng = random.Random(20261117)
    width, height = 400, 400
    raw = b"".join(
        b"\x00" + bytes(rng.getrandbits(8) for _ in range(width * 3))
        for _ in range(height)
    )

    def _chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 6))
        + _chunk(b"IEND", b"")
    )
    assert len(png) > 300 * 1024, f"构造的 PNG 太小，不具备真实量级: {len(png)}"

    base64_len = len(base64.b64encode(png))
    out = mcpContent.render_content_text(
        [
            {"type": "text", "text": "Took a screenshot of the current page's viewport."},
            _image_item(png),
        ],
        tool_name="take_screenshot",
    )
    print(f"\n[measure] png={len(png)} B, base64={base64_len} 字符, 改后返回={len(out)} 字符")
    _assert_no_base64(out)
    assert len(out) < 200, f"真实量级下上下文增量仍超预期: {len(out)} 字符"
    assert "[图片已保存: " in out
    assert "image/png" in out
