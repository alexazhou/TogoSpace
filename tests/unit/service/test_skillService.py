"""Tests for skillService: scan, index, query, and load Skill resources."""
import os
import tempfile

import pytest

import service.skillService as skillService
from service.skillService import SkillInfo, _parse_skill_md


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
        name, desc = _parse_skill_md(f.name)
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
        name, desc = _parse_skill_md(f.name)
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
        name, desc = _parse_skill_md(f.name)
    os.unlink(f.name)

    assert name == "test_skill"
    assert desc == ""


def test_parse_skill_md_no_front_matter():
    """没有 front-matter 时返回 (None, "")。"""
    content = "# Just a regular markdown\n\nNo front matter here."
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(content)
        f.flush()
        name, desc = _parse_skill_md(f.name)
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
        name, desc = _parse_skill_md(f.name)
    os.unlink(f.name)

    assert name is None


def test_parse_skill_md_nonexistent_file():
    """文件不存在时返回 (None, "")。"""
    name, desc = _parse_skill_md("/nonexistent/path/SKILL.md")
    assert name is None
    assert desc == ""


# ─── startup + registry tests ──────────────────────────────────


def test_startup_with_empty_dir():
    """skills 目录为空时索引也为空。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Patch _SKILLS_DIR to point to empty dir
        original = skillService._SKILLS_DIR
        skillService._SKILLS_DIR = tmpdir
        try:
            skillService.startup()
            assert skillService.get_all_skills() == []
            assert skillService.get_skill("nonexistent") is None
            assert skillService.is_valid_skill("nonexistent") is False
        finally:
            skillService._SKILLS_DIR = original


def test_startup_with_valid_skill():
    """包含有效 SKILL.md 的目录能被正确索引。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = os.path.join(tmpdir, "my_skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: my_skill\ndescription: 测试技能\n---\n\n# 内容\n")

        # 附加文件
        with open(os.path.join(skill_dir, "reference.txt"), "w", encoding="utf-8") as f:
            f.write("参考文件内容")

        original = skillService._SKILLS_DIR
        skillService._SKILLS_DIR = tmpdir
        try:
            skillService.startup()

            skills = skillService.get_all_skills()
            assert len(skills) == 1

            info = skillService.get_skill("my_skill")
            assert info is not None
            assert info.name == "my_skill"
            assert info.description == "测试技能"
            assert info.skill_dir == skill_dir
            assert "SKILL.md" in info.files
            assert "reference.txt" in info.files
        finally:
            skillService._SKILLS_DIR = original


def test_startup_skips_dir_without_skill_md():
    """缺少 SKILL.md 的子目录被跳过。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 子目录无 SKILL.md
        os.makedirs(os.path.join(tmpdir, "empty_skill"))

        original = skillService._SKILLS_DIR
        skillService._SKILLS_DIR = tmpdir
        try:
            skillService.startup()
            assert skillService.get_all_skills() == []
        finally:
            skillService._SKILLS_DIR = original


def test_startup_skips_invalid_front_matter():
    """SKILL.md 存在但 front-matter 无效时跳过。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = os.path.join(tmpdir, "bad_skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("# No front matter here\n")

        original = skillService._SKILLS_DIR
        skillService._SKILLS_DIR = tmpdir
        try:
            skillService.startup()
            assert skillService.get_all_skills() == []
        finally:
            skillService._SKILLS_DIR = original


def test_startup_name_mismatch_uses_dir_name():
    """name 与目录名不一致时使用目录名。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = os.path.join(tmpdir, "dir_name")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: different_name\ndescription: desc\n---\n\n# Content\n")

        original = skillService._SKILLS_DIR
        skillService._SKILLS_DIR = tmpdir
        try:
            skillService.startup()
            info = skillService.get_skill("dir_name")
            assert info is not None
            assert info.name == "dir_name"
        finally:
            skillService._SKILLS_DIR = original


def test_startup_nonexistent_dir():
    """skills 目录不存在时不报错，索引为空。"""
    original = skillService._SKILLS_DIR
    skillService._SKILLS_DIR = "/nonexistent/path/skills"
    try:
        skillService.startup()
        assert skillService.get_all_skills() == []
    finally:
        skillService._SKILLS_DIR = original


# ─── load_skill_content / load_skill_files tests ────────────────


def test_load_skill_content_returns_markdown():
    """加载 Skill 内容返回 SKILL.md 的完整文本。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = os.path.join(tmpdir, "readable_skill")
        os.makedirs(skill_dir)
        md_content = "---\nname: readable_skill\ndescription: 可读技能\n---\n\n# 正文内容\n"
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(md_content)

        original = skillService._SKILLS_DIR
        skillService._SKILLS_DIR = tmpdir
        try:
            skillService.startup()
            content = skillService.load_skill_content("readable_skill")
            assert content == md_content
        finally:
            skillService._SKILLS_DIR = original


def test_load_skill_content_nonexistent_returns_none():
    """加载不存在的 Skill 内容返回 None。"""
    original = skillService._SKILLS_DIR
    skillService._SKILLS_DIR = "/nonexistent/skills"
    try:
        skillService.startup()
        assert skillService.load_skill_content("no_such_skill") is None
    finally:
        skillService._SKILLS_DIR = original


def test_load_skill_files_returns_relative_paths():
    """load_skill_files 返回相对路径列表。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = os.path.join(tmpdir, "files_skill")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: files_skill\ndescription: desc\n---\n\n# Content\n")
        with open(os.path.join(skill_dir, "guide.md"), "w", encoding="utf-8") as f:
            f.write("Guide content")

        original = skillService._SKILLS_DIR
        skillService._SKILLS_DIR = tmpdir
        try:
            skillService.startup()
            files = skillService.load_skill_files("files_skill")
            assert files is not None
            assert "SKILL.md" in files
            assert "guide.md" in files
        finally:
            skillService._SKILLS_DIR = original


def test_load_skill_files_nonexistent_returns_none():
    """查询不存在的 Skill 文件列表返回 None。"""
    original = skillService._SKILLS_DIR
    skillService._SKILLS_DIR = "/nonexistent/skills"
    try:
        skillService.startup()
        assert skillService.load_skill_files("no_such_skill") is None
    finally:
        skillService._SKILLS_DIR = original


def test_startup_multiple_skills():
    """多个 Skill 目录都被正确索引。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        for name in ["skill_a", "skill_b", "skill_c"]:
            d = os.path.join(tmpdir, name)
            os.makedirs(d)
            with open(os.path.join(d, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(f"---\nname: {name}\ndescription: {name} desc\n---\n\n# {name}\n")

        original = skillService._SKILLS_DIR
        skillService._SKILLS_DIR = tmpdir
        try:
            skillService.startup()
            all_skills = skillService.get_all_skills()
            assert len(all_skills) == 3
            names = {s.name for s in all_skills}
            assert names == {"skill_a", "skill_b", "skill_c"}

            for name in names:
                assert skillService.is_valid_skill(name) is True
                assert skillService.get_skill(name) is not None
        finally:
            skillService._SKILLS_DIR = original