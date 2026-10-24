"""skillService 配置加载与索引构建的集成测试。

startup() 会重建进程级 Skill 索引，索引随后被 promptBuilder 消费，
因此这些用例属于服务级状态测试，放在 integration 由 ServiceTestCase 托管，
并在类结束时清空索引，避免把临时 Skill 带给后续测试。
"""
import os
import sys

import pytest

import appPaths
import service.skillService as skillService
from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")


class TestSkillServiceStartup(ServiceTestCase):
    @pytest.fixture(autouse=True)
    def skills_dirs(self, tmp_path, monkeypatch):
        """把扫描目录指向临时目录；monkeypatch 负责用例结束后还原 appPaths。"""
        builtin = tmp_path / "builtin"
        builtin.mkdir()
        monkeypatch.setattr(appPaths, "BUILTIN_SKILLS_DIR", str(builtin))
        monkeypatch.setattr(appPaths, "USER_SKILLS_DIR", str(tmp_path / "user"))
        return builtin

    @classmethod
    async def async_teardown_class(cls):
        # startup() 写入模块级索引，清空避免污染同 worker 的后续测试
        skillService.shutdown()

    def teardown_method(self):
        # startup() 会重建模块级索引，且 Running 态二次 startup 会抛 SERVICE_ALREADY_STARTED，
        # 因此每个用例结束后必须停机，保证下一个用例可以重新 startup。
        skillService.shutdown()

    def test_startup_with_empty_dir(self):
        """skills 目录为空时索引也为空。"""
        skillService.startup()

        assert skillService.get_all_skills() == []
        assert skillService.get_skill("nonexistent") is None
        assert skillService.is_valid_skill("nonexistent") is False

    def test_startup_with_valid_skill(self, skills_dirs):
        """包含有效 SKILL.md 的目录能被正确索引。"""
        skill_dir = skills_dirs / "my_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my_skill\ndescription: 测试技能\n---\n\n# 内容\n", encoding="utf-8"
        )
        (skill_dir / "reference.txt").write_text("参考文件内容", encoding="utf-8")

        skillService.startup()

        skills = skillService.get_all_skills()
        assert len(skills) == 1

        info = skillService.get_skill("my_skill")
        assert info is not None
        assert info.name == "my_skill"
        assert info.description == "测试技能"
        assert info.skill_dir == str(skill_dir)
        assert "SKILL.md" in info.files
        assert "reference.txt" in info.files

    def test_startup_skips_dir_without_skill_md(self, skills_dirs):
        """缺少 SKILL.md 的子目录被跳过。"""
        (skills_dirs / "empty_skill").mkdir()

        skillService.startup()

        assert skillService.get_all_skills() == []

    def test_startup_skips_invalid_front_matter(self, skills_dirs):
        """SKILL.md 存在但 front-matter 无效时跳过。"""
        skill_dir = skills_dirs / "bad_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# No front matter here\n", encoding="utf-8")

        skillService.startup()

        assert skillService.get_all_skills() == []

    def test_startup_name_mismatch_uses_dir_name(self, skills_dirs):
        """name 与目录名不一致时使用目录名。"""
        skill_dir = skills_dirs / "dir_name"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: different_name\ndescription: desc\n---\n\n# Content\n", encoding="utf-8"
        )

        skillService.startup()

        info = skillService.get_skill("dir_name")
        assert info is not None
        assert info.name == "dir_name"

    def test_startup_nonexistent_dir(self, tmp_path, monkeypatch):
        """skills 目录不存在时不报错，索引为空。"""
        monkeypatch.setattr(appPaths, "BUILTIN_SKILLS_DIR", str(tmp_path / "no_builtin"))
        monkeypatch.setattr(appPaths, "USER_SKILLS_DIR", str(tmp_path / "no_user"))

        skillService.startup()

        assert skillService.get_all_skills() == []

    def test_load_skill_content_returns_markdown(self, skills_dirs):
        """加载 Skill 内容返回 SKILL.md 的完整文本。"""
        skill_dir = skills_dirs / "readable_skill"
        skill_dir.mkdir()
        md_content = "---\nname: readable_skill\ndescription: 可读技能\n---\n\n# 正文内容\n"
        (skill_dir / "SKILL.md").write_text(md_content, encoding="utf-8")

        skillService.startup()

        info = skillService.get_skill("readable_skill")
        assert info is not None
        assert info.content == md_content

    def test_load_skill_content_nonexistent_returns_none(self):
        """加载不存在的 Skill 内容返回 None。"""
        skillService.startup()

        assert skillService.get_skill("no_such_skill") is None

    def test_load_skill_files_returns_relative_paths(self, skills_dirs):
        """load_skill_files 返回相对路径列表。"""
        skill_dir = skills_dirs / "files_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: files_skill\ndescription: desc\n---\n\n# Content\n", encoding="utf-8"
        )
        (skill_dir / "guide.md").write_text("Guide content", encoding="utf-8")

        skillService.startup()

        info = skillService.get_skill("files_skill")
        assert info is not None
        assert "SKILL.md" in info.files
        assert "guide.md" in info.files

    def test_load_skill_files_nonexistent_returns_none(self):
        """查询不存在的 Skill 文件列表返回 None。"""
        skillService.startup()

        assert skillService.get_skill("no_such_skill") is None

    def test_startup_multiple_skills(self, skills_dirs):
        """多个 Skill 目录都被正确索引。"""
        for name in ["skill_a", "skill_b", "skill_c"]:
            d = skills_dirs / name
            d.mkdir()
            (d / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {name} desc\n---\n\n# {name}\n", encoding="utf-8"
            )

        skillService.startup()

        all_skills = skillService.get_all_skills()
        assert len(all_skills) == 3
        names = {s.name for s in all_skills}
        assert names == {"skill_a", "skill_b", "skill_c"}

        for name in names:
            assert skillService.is_valid_skill(name) is True
            assert skillService.get_skill(name) is not None
