"""`mcp resource <server> <uri>` 读**二进制资源**：落盘 + 一行事实型提示（B66 / #302）。

## 改前 / 改后

改前：`_cmd_resource` 的二进制分支只输出 `"<binary {N} bytes>"` —— 只有长度，内容既看不见
也拿不到（agent 白读一次）。改后：复用 #300 的落盘入口（同一 temp 目录、同一
`mcp-{name}-{uuid8}.{ext}` 命名、同一 mimeType→扩展名映射），上下文只留
`[资源已保存: <绝对路径> (size, mimeType)]`，零 base64、零正文、无使用指引。

## 三条「不动」的回归

- 文本资源：原文照出，**逐字节不变**；
- `--output json`：`contents[].blob` 完整 base64（显式 opt-in），**不动**；
- 资源列表模式（只给 uri + name）：**不动**。

## 举证口径

server 是**自造**的（FastMCP + 真 PNG 字节 + 文本资源），环境无 `chrome-devtools`。
本文件**不声称**对真实 `chrome-devtools` 实测。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from container_db_schema import migrate_all
from model.dbModel.base import bind_database, unbind_database
from service import mcpSessionService as svc
from service.agentService import toolRegistry
from service.funcToolService import core as func_tool_core
from service.ormService import AioSqliteDatabase
from util import llmApiUtil

_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{100,}={0,2}")

#: 自造 MCP server：一个二进制资源（真 PNG，随机像素、体积接近真实截图）+ 一个文本资源。
_STDIO_SERVER = '''
import random
import struct
import zlib

from mcp.server.fastmcp import FastMCP


def _make_png(width=120, height=120):
    rng = random.Random(302)
    raw = b"".join(
        b"\\x00" + bytes(rng.getrandbits(8) for _ in range(width * 3))
        for _ in range(height)
    )

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\\x89PNG\\r\\n\\x1a\\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


PNG = _make_png()

mcp = FastMCP("res")


@mcp.resource("shot://raw", mime_type="image/png")
def raw() -> bytes:
    """二进制资源（真 PNG 字节）"""
    return PNG


@mcp.resource("note://hello")
def hello() -> str:
    """文本资源（用于回归：必须逐字节照出）"""
    return "hi from resource"


mcp.run()
'''


def _stdio_command(tmp_path: Path) -> str:
    script = tmp_path / "server_res.py"
    script.write_text(_STDIO_SERVER, encoding="utf-8")
    return f"{sys.executable} {script}"


@pytest.fixture
async def mcp_db(tmp_path: Path):
    db_path = tmp_path / "mcp_resource_dump.db"
    migrate_all(db_path, workspace_root=tmp_path)
    db = AioSqliteDatabase(str(db_path), pool_params={"timeout": 30})
    bind_database(db)
    await db.aio_connect()
    try:
        yield db_path
    finally:
        await svc.shutdown()
        svc.reset_for_tests()
        await db.aio_close()
        unbind_database()


@pytest.fixture
def leader_ctx():
    return toolRegistry.ToolCallContext(agent_id=1, team_id=1, chat_room=None)


@pytest.fixture
def loaded_tools():
    func_tool_core.load_func_tools()
    func_tool_core.load_container_func_tools()


def _build_registry() -> toolRegistry.AgentToolRegistry:
    registry = toolRegistry.AgentToolRegistry()
    for tool in func_tool_core.get_all_tools():
        registry.register(tool, func_tool_core.run_tool_call)
    return registry


def _console_call(call_id: str, command: str) -> llmApiUtil.OpenAIToolCall:
    return llmApiUtil.OpenAIToolCall(
        id=call_id,
        function={"name": "console", "arguments": json.dumps({"command": command})},
    )


def _assert_no_base64(text: str) -> None:
    assert "iVBORw0KGgo" not in text, f"PNG base64 前缀仍在返回体: {text[:200]}"
    match = _BASE64_RUN.search(text)
    assert match is None, f"返回体仍有 base64 连续段（{len(match.group(0))} 字符）"


def _saved_path(text: str) -> Path:
    match = re.search(r"\[资源已保存: ([^ ]+) ", text)
    assert match, f"返回体里没有 `[资源已保存: <path>` 提示: {text[:200]}"
    return Path(match.group(1))


def _file_kind(path: Path) -> str:
    done = subprocess.run(["file", "-b", str(path)], capture_output=True, text=True)
    return done.stdout.strip()


async def _run(registry, ctx, call_id: str, console_command: str) -> dict:
    result = await registry.execute_tool_call(
        _console_call(call_id, console_command), context=ctx
    )
    return result.result


@pytest.mark.asyncio
async def test_console_resource_binary_saved_with_prompt(
    mcp_db, leader_ctx, loaded_tools, tmp_path: Path
):
    """B66 主判据：二进制资源 ⇒ 落盘 + 一行提示，零 base64、零正文。

    改前这里是 `<binary N bytes>`（拿不到文件）—— 同一用例搬到基线必红。
    """
    command = _stdio_command(tmp_path)
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )
    assert (
        await _run(registry, leader_ctx, "b_add", f"mcp config add res '{command}'")
    ).get("success") is True

    body = await _run(registry, leader_ctx, "b_read", "mcp resource res shot://raw")
    assert body.get("success") is True, body
    text = body["text"]

    _assert_no_base64(text)
    assert "<binary" not in text, "改前的占位符不得再出现"
    assert "[资源已保存: " in text

    saved = _saved_path(text)
    assert saved.is_absolute()
    assert saved.suffix == ".png", text
    payload = saved.read_bytes()
    assert payload.startswith(b"\x89PNG\r\n\x1a\n"), "落盘内容必须是真 PNG"
    assert "PNG image data" in _file_kind(saved), _file_kind(saved)
    assert "image/png" in text

    print(
        f"\n[measure] 返回文本字符数={len(text)} 落盘字节={len(payload)} "
        f"base64 若内联约={len(payload) * 4 // 3}"
    )
    assert len(text) < 250, f"上下文增量超预期: {len(text)} 字符"

    await _run(registry, leader_ctx, "b_rm", "mcp config remove res")


@pytest.mark.asyncio
async def test_console_resource_text_unchanged(
    mcp_db, leader_ctx, loaded_tools, tmp_path: Path
):
    """文本资源：原文照出、**逐字节不变**（B66 明确的不动项）。"""
    command = _stdio_command(tmp_path)
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )
    assert (
        await _run(registry, leader_ctx, "t_add", f"mcp config add res '{command}'")
    ).get("success") is True

    body = await _run(registry, leader_ctx, "t_read", "mcp resource res note://hello")
    assert body.get("success") is True, body
    assert body["text"] == "hi from resource", body["text"]
    assert "[资源已保存: " not in body["text"], "文本资源不得被落盘改写"
    print(f"\n[measure] 文本资源 text={body['text']!r}")

    await _run(registry, leader_ctx, "t_rm", "mcp config remove res")


@pytest.mark.asyncio
async def test_console_resource_json_output_unchanged(
    mcp_db, leader_ctx, loaded_tools, tmp_path: Path
):
    """`--output json`：`contents[].blob` 完整 base64（显式 opt-in），不动。

    打印 sha256，供「基线 vs HEAD」两树逐字节比对。
    """
    command = _stdio_command(tmp_path)
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )
    assert (
        await _run(registry, leader_ctx, "j_add", f"mcp config add res '{command}'")
    ).get("success") is True

    body = await _run(
        registry, leader_ctx, "j_read", "mcp resource res shot://raw --output json"
    )
    assert body.get("success") is True, body
    contents = body["contents"]
    assert len(contents) == 1
    assert contents[0]["uri"] == "shot://raw"
    assert contents[0]["mimeType"] == "image/png"
    assert "blob" in contents[0] and len(contents[0]["blob"]) > 100, "json 必须保留完整 base64"
    digest = hashlib.sha256(
        json.dumps(contents, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    print(f"\n[measure] json contents sha256={digest}")
    assert "text" not in body, "json 模式不应额外拼 text"

    await _run(registry, leader_ctx, "j_rm", "mcp config remove res")


@pytest.mark.asyncio
async def test_console_resource_list_mode_unchanged(
    mcp_db, leader_ctx, loaded_tools, tmp_path: Path
):
    """资源列表模式（只给 uri + name）：不动。"""
    command = _stdio_command(tmp_path)
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )
    assert (
        await _run(registry, leader_ctx, "l_add", f"mcp config add res '{command}'")
    ).get("success") is True

    body = await _run(registry, leader_ctx, "l_list", "mcp resource res")
    assert body.get("success") is True, body
    assert body["count"] == 2, body
    assert "shot://raw" in body["text"] and "note://hello" in body["text"]
    assert "[资源已保存: " not in body["text"], "列表模式不得落盘"

    body_json = await _run(
        registry, leader_ctx, "l_list_json", "mcp resource res --output json"
    )
    assert body_json.get("success") is True, body_json
    digest = hashlib.sha256(
        json.dumps(body_json["resources"], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    print(f"\n[measure] 列表 resources sha256={digest}")

    await _run(registry, leader_ctx, "l_rm", "mcp config remove res")
