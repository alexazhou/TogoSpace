#!/usr/bin/env python3
"""
工具 schema 快照护栏：把 `tools.py` 里所有本地工具的 description 与参数说明固化成
`tests/fixtures/tool_schema_snapshot.json`，供
`tests/unit/service/funcToolService/test_tool_schema_snapshot.py` 逐字段对比。

为什么不用哈希指纹：哈希口径无法跨人复现（同一份代码换个序列化就出两个值，实测同一
份代码出现过 `47e5c865` 与 `f82bede4` 两个指纹），而且只告诉你「变了」、不告诉你
「哪句变了」。工具 schema 是模型看到的产品接口，变化必须显式、可读、有意为之 ——
所以这里输出逐工具的人类可读 diff。

用法：
  python scripts/tool_schema_snapshot.py            # 与快照比较；有差异打印 diff 并 exit 1
  python scripts/tool_schema_snapshot.py --update    # 重写快照（唯一合法更新入口）
  python scripts/tool_schema_snapshot.py --print     # 只打印当前快照 JSON（不比较）

写工具 docstring 的两条口径（#72 收口时定）：
  · 私有参数（`_context` 等）不要写进 `Args:` 段 —— 它们不在签名合法参数名集合里，
    会被当成续行并入上一个参数的说明；
  · 参数说明里别夹 `NOTE:` / `TODO:` 这类标签行 —— 想给模型看的内容写首段或参数说明本身。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
SRC_DIR = os.path.join(REPO_ROOT, "src")
SNAPSHOT_PATH = os.path.join(REPO_ROOT, "tests", "fixtures", "tool_schema_snapshot.json")
SNAPSHOT_REL_PATH = os.path.relpath(SNAPSHOT_PATH, REPO_ROOT)


def _ensure_src_on_path() -> None:
    if SRC_DIR not in sys.path:
        sys.path.insert(0, SRC_DIR)


def build_snapshot() -> dict:
    """计算当前快照：`{工具名: {"description": str, "parameters": {参数名: 说明}}}`。

    口径（必须确定性，任何人任何机器跑都一样）：
    - 只算 `CATEGORY_CONFIG` 中**确实存在于 tools.py** 的工具（TSP 等外部工具一律跳过）；
    - 工具名排序；每个工具记 description 与其全部 properties 的 `{参数名: description}`；
    - **不写入**时间戳、绝对路径、commit、Python/依赖版本等易变信息。
    """
    _ensure_src_on_path()

    from service.agentService.toolRegistry import CATEGORY_CONFIG
    from service.funcToolService import tools
    from service.funcToolService.funcToolType import get_function_metadata

    snapshot: dict[str, dict] = {}
    for tool_name in sorted(CATEGORY_CONFIG):
        func = getattr(tools, tool_name, None)
        if func is None:  # 非 tools.py 内的本地工具（如 TSP）
            continue
        metadata = get_function_metadata(tool_name, func)
        snapshot[tool_name] = {
            "description": metadata["description"],
            "parameters": {
                param_name: (param_schema.get("description") or "")
                for param_name, param_schema in metadata["parameters"]["properties"].items()
            },
        }
    return snapshot


def dumps(snapshot: dict) -> str:
    """确定性的序列化（排序键 + 2 空格缩进 + 末尾换行）。"""
    return json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load_snapshot(path: str = SNAPSHOT_PATH) -> dict:
    """读取已提交的快照；文件缺失时报错（提示用 --update 生成）。"""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"快照文件不存在：{path}；请先运行 `python scripts/tool_schema_snapshot.py --update`"
        )
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def diff_snapshots(expected: dict, actual: dict) -> list[str]:
    """逐工具生成人类可读 diff（改前→改后），返回行列表；无差异返回空列表。"""
    lines: list[str] = []

    for tool_name in sorted(set(expected) - set(actual)):
        lines.append(f"[{tool_name}] 工具已删除（快照里有、代码里没有）")

    for tool_name in sorted(set(actual) - set(expected)):
        lines.append(f"[{tool_name}] 工具已新增（代码里有、快照里没有）")

    for tool_name in sorted(set(expected) & set(actual)):
        old_tool = expected[tool_name] or {}
        new_tool = actual[tool_name] or {}

        old_desc = old_tool.get("description") or ""
        new_desc = new_tool.get("description") or ""
        if old_desc != new_desc:
            lines.append(f"[{tool_name}] description 变化：")
            lines.append(f"  - 旧: {old_desc!r}")
            lines.append(f"  + 新: {new_desc!r}")

        old_params = old_tool.get("parameters") or {}
        new_params = new_tool.get("parameters") or {}

        for param_name in sorted(set(old_params) - set(new_params)):
            lines.append(f"[{tool_name}.{param_name}] 参数已移除（旧说明: {old_params[param_name]!r}）")

        for param_name in sorted(set(new_params) - set(old_params)):
            lines.append(f"[{tool_name}.{param_name}] 参数已新增（说明: {new_params[param_name]!r}）")

        for param_name in sorted(set(old_params) & set(new_params)):
            if old_params[param_name] != new_params[param_name]:
                lines.append(f"[{tool_name}.{param_name}] 参数说明变化：")
                lines.append(f"  - 旧: {old_params[param_name]!r}")
                lines.append(f"  + 新: {new_params[param_name]!r}")

    return lines


def format_diff(expected: dict, actual: dict) -> str:
    """把 diff 渲染成一段文本（CLI 与测试共用；**禁止**用哈希替代）。"""
    lines = diff_snapshots(expected, actual)
    if not lines:
        return f"OK（{len(actual)} 个工具，schema 与快照一致）"
    header = (
        "工具 schema 与快照不一致。若为有意变更，请运行\n"
        "  python scripts/tool_schema_snapshot.py --update\n"
        f"并在 commit message 里说明变更原因（快照文件：{SNAPSHOT_REL_PATH}）。\n"
    )
    return header + "\n" + "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="对比/更新工具 schema 快照（description + 参数说明）。",
    )
    parser.add_argument(
        "--update", action="store_true",
        help="重写 tests/fixtures/tool_schema_snapshot.json（唯一合法的快照更新入口）",
    )
    parser.add_argument(
        "--print", dest="print_only", action="store_true",
        help="只打印当前快照 JSON，不与已提交快照比较",
    )
    args = parser.parse_args(argv)

    actual = build_snapshot()

    if args.print_only:
        print(dumps(actual), end="")
        return 0

    if args.update:
        os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
        with open(SNAPSHOT_PATH, "w", encoding="utf-8") as file:
            file.write(dumps(actual))
        print(f"✅ 已更新快照 {SNAPSHOT_REL_PATH}（{len(actual)} 个工具）")
        return 0

    expected = load_snapshot()
    diff_lines = diff_snapshots(expected, actual)
    if diff_lines:
        print("❌ " + format_diff(expected, actual))
        return 1

    print(f"OK（{len(actual)} 个工具）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
