"""Team / Agent 名称的目录安全校验。"""

import pytest

from util import workspaceUtil


@pytest.mark.parametrize(
    "name",
    [
        "alice",
        "Alice_01",
        "alice_bob",
        "产品经理",
        "UI设计师",
        "a1",
        "alice bob",
        "alice-bob",
        "alice.txt",
        "😀",
        "CON1",
        "myCON",
        "Ｆ",
        "²",
        "a..b",
    ],
)
def test_valid_dir_name_passes(name: str) -> None:
    """黑名单之外的名称（含 emoji、连字符、点号、中间空格）都可以直接作为目录名。"""
    assert workspaceUtil.validate_dir_name(name) is None


@pytest.mark.parametrize(
    "name",
    [
        "",
        " alice",
        "alice ",
        "../alice",
        "alice/bob",
        "alice\\bob",
        "alice:bob",
        "alice*bob",
        "alice?bob",
        "alice|bob",
        'alice"bob',
        "alice<bob",
        "alice>bob",
        ".",
        "..",
        "CON",
        "con",
        "com1",
        "CON.txt",
        "NUL.abc",
        "a\x00b",
        "a\tb",
    ],
)
def test_invalid_dir_name_rejected(name: str) -> None:
    """空白、路径分隔符、Windows 非法字符、保留名（含扩展名）和控制字符都应被拒绝。"""
    assert workspaceUtil.validate_dir_name(name) is not None


def test_non_string_name_rejected_without_exception() -> None:
    """校验函数不抛异常，非字符串输入也通过返回值表达。"""
    message = workspaceUtil.validate_dir_name(None)  # type: ignore[arg-type]
    assert message == "必须是字符串"
