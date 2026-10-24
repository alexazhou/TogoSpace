"""skillService 纯解析逻辑的单元测试（不触碰全局索引、不启动服务）。"""
import os
import tempfile

from service.skillService import _parse_skill_md


# ─── _parse_skill_md tests ─────────────────────────────────────


def test_parse_skill_md_valid():
    """Standard SKILL.md with name and description."""
    content = """---
name: code_review
description: 代码审查技能包
---

# 代码审查技能

## 审查规范

1. 检查命名一致性
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        name, desc, parsed_content = _parse_skill_md(f.name)
    os.unlink(f.name)

    assert name == "code_review"
    assert desc == "代码审查技能包"


def test_parse_skill_md_missing_name():
    """front-matter 中没有 name 字段，返回 (None, ...)。"""
    content = """---
description: 某个技能
---

内容
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        name, desc, parsed_content = _parse_skill_md(f.name)
    os.unlink(f.name)

    assert name is None
    assert desc == "某个技能"


def test_parse_skill_md_missing_description():
    """缺少 description 时默认为空字符串。"""
    content = """---
name: test_skill
---

内容
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        name, desc, parsed_content = _parse_skill_md(f.name)
    os.unlink(f.name)

    assert name == "test_skill"
    assert desc == ""


def test_parse_skill_md_no_front_matter():
    """没有 front-matter 时返回 (None, "")。"""
    content = "# Just a regular markdown\n\nNo front matter here."
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        name, desc, parsed_content = _parse_skill_md(f.name)
    os.unlink(f.name)

    assert name is None
    assert desc == ""


def test_parse_skill_md_unclosed_front_matter():
    """front-matter 未闭合（只有开头 --- 没有结尾 ---）。"""
    content = """---
name: broken
description: 无闭合标记
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        name, desc, parsed_content = _parse_skill_md(f.name)
    os.unlink(f.name)

    assert name is None


def test_parse_skill_md_nonexistent_file():
    """文件不存在时返回 (None, "")。"""
    name, desc, parsed_content = _parse_skill_md("/nonexistent/path/SKILL.md")
    assert name is None
    assert desc == ""

