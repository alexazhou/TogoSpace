"""console `mcp call` × 真 stdio 子进程 × 多模态 content（need_fix_3 §6 端到端判据）。

## 这个文件钉的是什么

改造前 `_content_text()` 把 `CallToolResult.content[]` 拍平成文本时，对 `image` 段取
`data`（base64）直接拼进去 ⇒ 一次截图调用给上下文灌近 1MB 裸 base64。本文件走**完整
console 链路**（`mcp config add` → 真 stdio 子进程 → `call_tool` → 结果渲染）验证：
落盘 + 路径占位 + **零 base64**，并且文本类工具行为不变、`--output json` 结构不变。

## 举证口径（本单红线）

这里的 MCP server 是**自造**的（`_STDIO_SERVER`：FastMCP + 真 PNG/JPEG 字节），返回
与 `chrome-devtools-mcp` **同构**的 `content[]`（`text` + `image`）。本文件**不声称**
对真实 `chrome-devtools` 做过实测 —— 环境里没有该 server。报告里也按「自造 server」
标注，不得写成对真实工具的实测通过。

## 为什么断言「零 base64」而不是「有路径」

只断言「文本里有 `.png`」是不够的：一个半吊子实现完全可以在拼上路径的同时把 base64
留在后面。所以反判据用「100+ 字符 base64 连续段」正则 + PNG magic 前缀双重检查，
任何一段漏网都会红。
"""

from __future__ import annotations

import base64
import hashlib
import json
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

#: 自造 MCP server：返回 chrome-devtools 形态的 `content[]`（说明文本 + image 段）。
#: 图片字节在 server 端用 zlib 现场构造，保证是**真 PNG**；JPEG 用最小合法段。
_STDIO_SERVER = '''
import struct
import zlib

from mcp.server.fastmcp import FastMCP, Image


def _make_png(width=8, height=8):
    raw = b"".join(b"\\x00" + b"\\x00\\x80\\xff" * width for _ in range(height))

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
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


PNG = _make_png()
JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300"
    + "ff" * 64
    + "ffc2000b080001000101011100ffc40014000100000000000000000000000000000009"
    + "ffda0008010100003f00d2cf20ffd9"
)

mcp = FastMCP("shot")


@mcp.tool()
def take_screenshot(format: str = "png") -> list:
    """截图（自造：与 chrome-devtools 同构，返回 text + image 两段）"""
    head = "Took a screenshot of the current page's viewport."
    if format == "jpeg":
        return [head, Image(data=JPEG, format="jpeg")]
    return [head, Image(data=PNG, format="png")]


@mcp.tool()
def echo_text(x: str) -> str:
    """纯文本工具（回归：文本路径行为必须不变）"""
    return "echo:" + x


mcp.run()
'''


def _stdio_command(tmp_path: Path) -> str:
    script = tmp_path / "server_shot.py"
    script.write_text(_STDIO_SERVER, encoding="utf-8")
    return f"{sys.executable} {script}"


@pytest.fixture
async def mcp_db(tmp_path: Path):
    db_path = tmp_path / "mcp_multimodal.db"
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


def _saved_path(text: str, label: str = "图片") -> Path:
    match = re.search(rf"\[{label}已保存: ([^ ]+) ", text)
    assert match, f"返回体里没有 `[{label}已保存: <path>` 占位: {text[:200]}"
    return Path(match.group(1))


def _file_kind(path: Path) -> str:
    """本机 `file(1)` 的实际识别结果（不依赖 PIL；仓库未装图像库）。"""
    done = subprocess.run(["file", "-b", str(path)], capture_output=True, text=True)
    return done.stdout.strip()


@pytest.mark.asyncio
async def test_console_call_screenshot_saves_file_and_no_base64(
    mcp_db, leader_ctx, loaded_tools, tmp_path: Path
):
    """§6.1 + §6.2：不传 filePath ⇒ 落盘 + 路径占位 + **零 base64** + 上下文增量 <200。

    自造 server 测得（非真实 chrome-devtools）。
    """
    command = _stdio_command(tmp_path)
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )

    async def _run(call_id: str, console_command: str) -> dict:
        result = await registry.execute_tool_call(
            _console_call(call_id, console_command), context=leader_ctx
        )
        return result.result

    added = await _run("s_add", f"mcp config add shot '{command}'")
    assert added.get("success") is True, added

    called = await _run("s_call", "mcp call shot.take_screenshot")
    assert called.get("success") is True, called
    text = called["text"]

    _assert_no_base64(text)
    assert "Took a screenshot of the current page's viewport." in text

    saved = _saved_path(text)
    assert saved.is_absolute()
    assert saved.suffix == ".png"
    assert saved.name.startswith("mcp-take_screenshot-")
    payload = saved.read_bytes()
    assert payload.startswith(b"\x89PNG\r\n\x1a\n"), "落盘内容必须是真 PNG"
    assert saved.stat().st_size == len(payload)

    # 扩展名与实际格式一致（file(1) 实测，独立于我们的 magic 判定）
    assert "PNG image data" in _file_kind(saved), _file_kind(saved)

    # 上下文增量：整段返回文本的字符数（改前是「说明 + 近 1MB base64」）
    print(f"\n[measure] 返回文本字符数={len(text)}")
    print(f"[measure] 落盘字节数={len(payload)}  base64 若内联约={len(base64.b64encode(payload))}")
    assert len(text) < 200, f"上下文增量超预期: {len(text)} 字符"

    # 清理全局注册项，避免影响同进程其他用例
    removed = await _run("s_rm", "mcp config remove shot")
    assert removed.get("success") is True, removed


@pytest.mark.asyncio
async def test_console_call_screenshot_jpeg_extension(
    mcp_db, leader_ctx, loaded_tools, tmp_path: Path
):
    """§6.3：`--args '{"format":"jpeg"}'` ⇒ 扩展名 `.jpg`，且 file(1) 认 JPEG。

    自造 server 测得（非真实 chrome-devtools）。
    """
    command = _stdio_command(tmp_path)
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )

    async def _run(call_id: str, console_command: str) -> dict:
        result = await registry.execute_tool_call(
            _console_call(call_id, console_command), context=leader_ctx
        )
        return result.result

    assert (await _run("j_add", f"mcp config add shot '{command}'")).get("success") is True

    called = await _run(
        "j_call", "mcp call shot.take_screenshot --args '{\"format\":\"jpeg\"}'"
    )
    assert called.get("success") is True, called
    text = called["text"]

    _assert_no_base64(text)
    saved = _saved_path(text)
    assert saved.suffix == ".jpg", text
    assert saved.read_bytes().startswith(b"\xff\xd8\xff")
    assert "JPEG image data" in _file_kind(saved), _file_kind(saved)

    await _run("j_rm", "mcp config remove shot")


@pytest.mark.asyncio
async def test_console_call_text_tool_unchanged_and_json_structure_intact(
    mcp_db, leader_ctx, loaded_tools, tmp_path: Path
):
    """§6.4 + §6.5：纯文本工具返回逐字节不变；`--output json` 的 content 结构不变。

    json 路径改造前后都应保留完整 `content[]`（含 image 段 base64）——这里同时打印
    content 的 sha256，供两棵树（base / head）比对「逐字节不变」。
    """
    command = _stdio_command(tmp_path)
    registry = _build_registry()
    registry.apply_tool_allow_specs(
        ["Category:Basic", "Category:Read", "Category:Write", "Category:Execute"]
    )

    async def _run(call_id: str, console_command: str) -> dict:
        result = await registry.execute_tool_call(
            _console_call(call_id, console_command), context=leader_ctx
        )
        return result.result

    assert (await _run("r_add", f"mcp config add shot '{command}'")).get("success") is True

    echo = await _run("r_echo", "mcp call shot.echo_text x=hi")
    assert echo.get("success") is True, echo
    assert echo["text"] == "echo:hi", "文本类工具返回必须逐字节不变"

    js = await _run("r_json", "mcp call shot.take_screenshot --output json")
    assert js.get("success") is True, js
    content = js["content"]
    assert [item["type"] for item in content] == ["text", "image"], content
    assert content[1]["mimeType"] == "image/png"
    assert "data" in content[1] and len(content[1]["data"]) >= 100, "json 路径必须保留完整结构"
    digest = hashlib.sha256(
        json.dumps(content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    print(f"\n[measure] json content sha256={digest}")
    assert "text" not in js, "json 模式下不应再多拼一份 text（改前也没有）"

    await _run("r_rm", "mcp config remove shot")
