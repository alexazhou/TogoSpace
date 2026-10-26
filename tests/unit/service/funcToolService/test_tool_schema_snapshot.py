"""#73 / #75：工具 schema 快照护栏（替代不可复现的哈希指纹）。

把 `tools.py` 里每个本地工具的 **description、参数说明、参数 JSON Schema
（type / enum / array…）与工具级 required**，与已提交的
`tests/fixtures/tool_schema_snapshot.json` 逐字段对比。任何变化都会让本测试变红，
且失败信息是**结构化 diff**（哪个工具的哪句话 / 哪个参数的 schema / required 变了，
改前→改后），**禁止只给哈希**。

如为有意变更：运行

    python scripts/tool_schema_snapshot.py --update

更新快照，**并在 commit message 里说明变更原因**。本测试**不自动更新快照**
（不允许 CI 静默改写）；`--update` 是唯一合法的快照更新入口。

顺带记两条写工具 docstring 的口径（#72 收口时定）：
- 私有参数（`_context` 等）不要写进 `Args:` 段 —— 不在签名合法参数名集合里，
  会被当成续行并入上一个参数的说明；
- 参数说明里别夹 `NOTE:` / `TODO:` 这类标签行 —— 想给模型看的内容写首段或参数说明本身。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_PATH = REPO_ROOT / "scripts" / "tool_schema_snapshot.py"


def _load_snapshot_module():
    """按文件路径加载 `scripts/tool_schema_snapshot.py`（复用同一份口径，避免各写一套）。"""
    spec = importlib.util.spec_from_file_location("tool_schema_snapshot", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"无法加载脚本：{SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_schema_matches_committed_snapshot():
    """工具 schema（description + 每个参数的说明）必须与快照文件完全一致。

    失败时输出的是逐工具的结构化 diff（改前→改后），而不是哈希值 —— 目的是让人
    一眼看出「模型的工具说明到底哪句被改了」，而不是只知道「变了」。
    """
    module = _load_snapshot_module()

    expected = module.load_snapshot()
    actual = module.build_snapshot()

    assert actual == expected, module.format_diff(expected, actual)
