"""`_cmd_resource` 二进制分支的两条**失败/退化路径**（#302 / B66）。

端到端在 `tests/integration/test_funcToolService/test_mcp_resource_dump.py` 用真 stdio
server 覆盖；这里用 stub 连接直接打在 `_cmd_resource` 的渲染逻辑上，专门造**失败场景**：

1. blob 不是合法 base64 ⇒ 必须给 `[资源无法解码: …]`，**原始内容一个字符都不回吐**、
   **不得落盘**（失败时回吐/半落盘都会让「零 base64」的收益打折，与 #300 同口径）；
2. 既无 `text` 又无 `blob`（协议上不该出现）⇒ 保持改前的 `<binary 0 bytes>` 占位，
   不新造行为。

> 为什么单独造这两条：B66 的正面判据只证明「正常二进制会落盘」；把 `_save_binary` 的失败
> 分支改成回吐原始 data、或把空资源也拉去落盘，正面用例都不会红。
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from service.funcToolService import mcpConsole


@pytest.fixture(autouse=True)
def _isolated_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """落盘目录指向 tmp_path：便于断言「失败路径没有产生文件」。"""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture
def stub_connection(monkeypatch: pytest.MonkeyPatch):
    """把 server 解析与协议调用都换掉，只留 `_cmd_resource` 的渲染路径。"""
    state: dict[str, object] = {}

    async def _resolve(name: str):
        return SimpleNamespace(display=name)

    async def _with_connection(context, ref, action, **kwargs):
        return state["result"]

    monkeypatch.setattr(mcpConsole.sessions, "resolve_ref", _resolve)
    monkeypatch.setattr(mcpConsole, "_with_connection", _with_connection)
    return state


def _attach(state: dict, items: list) -> None:
    state["result"] = SimpleNamespace(contents=items)


@pytest.mark.asyncio
async def test_invalid_blob_not_leaked_and_not_written(
    stub_connection, tmp_path: Path
):
    """非法 base64 ⇒ 只给「无法解码」，不回吐原文、不落盘。"""
    bad = "!!!not-base64!!!" * 20
    _attach(
        stub_connection,
        [SimpleNamespace(uri="res://bad", mimeType="image/png", blob=bad)],
    )

    body = await mcpConsole._cmd_resource(["res", "res://bad"], {}, SimpleNamespace())

    text = body["text"]
    assert text.startswith("[资源无法解码: image/png"), text
    assert "not-base64" not in text, "失败路径不得回吐原始内容"
    assert str(len(bad)) in text, "至少要给出长度，便于判断规模"
    assert list(tmp_path.glob("mcp-*")) == [], "解码失败不得落盘"


@pytest.mark.asyncio
async def test_empty_binary_keeps_legacy_placeholder(stub_connection, tmp_path: Path):
    """既无 text 又无 blob ⇒ 保持改前的 `<binary 0 bytes>`，不新造行为。"""
    _attach(stub_connection, [SimpleNamespace(uri="res://empty", mimeType="image/png")])

    body = await mcpConsole._cmd_resource(["res", "res://empty"], {}, SimpleNamespace())

    assert body["text"] == "<binary 0 bytes>", body["text"]
    assert list(tmp_path.glob("mcp-*")) == []


@pytest.mark.asyncio
async def test_binary_filename_carries_server_name(stub_connection, tmp_path: Path):
    """落盘文件名带 server 名（`mcp-{server}-{uuid8}.{ext}`），便于从 temp 溯源。"""
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    _attach(
        stub_connection,
        [
            SimpleNamespace(
                uri="res://raw",
                mimeType="image/png",
                blob=base64.b64encode(png).decode(),
            )
        ],
    )

    body = await mcpConsole._cmd_resource(["my-server", "res://raw"], {}, SimpleNamespace())

    saved = Path(body["text"].split(": ", 1)[1].split(" ")[0])
    assert saved.parent == tmp_path
    assert saved.name.startswith("mcp-my-server-"), saved.name
    assert saved.read_bytes() == png
