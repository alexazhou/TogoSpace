"""Skill 服务：扫描、索引、查询、加载 Skill 资源。"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

_SKILLS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "assets", "skills")

_SKILL_MD = "SKILL.md"


@dataclass
class SkillInfo:
    """Skill 的索引信息，启动时扫描生成。"""
    name: str
    description: str
    skill_dir: str
    files: list[str] = field(default_factory=list)


_registry: dict[str, SkillInfo] = {}


def startup() -> None:
    """扫描 assets/skills/ 目录，构建全局 Skill 索引。"""
    global _registry
    _registry = {}

    if not os.path.isdir(_SKILLS_DIR):
        logger.info("Skill 目录不存在，跳过扫描: %s", _SKILLS_DIR)
        return

    for entry in os.listdir(_SKILLS_DIR):
        skill_dir = os.path.join(_SKILLS_DIR, entry)
        if not os.path.isdir(skill_dir):
            continue

        skill_md_path = os.path.join(skill_dir, _SKILL_MD)
        if not os.path.isfile(skill_md_path):
            logger.warning("Skill 目录 '%s' 缺少 %s，跳过", entry, _SKILL_MD)
            continue

        name, description = _parse_skill_md(skill_md_path)
        if name is None:
            logger.warning("Skill '%s' 的 %s 缺少有效的 front-matter，跳过", entry, _SKILL_MD)
            continue

        if name != entry:
            logger.warning("Skill '%s' 的 name '%s' 与目录名不一致，使用目录名", entry, name)
            name = entry

        # 收集目录下的相对文件路径
        files = []
        for root, _dirs, filenames in os.walk(skill_dir):
            for filename in filenames:
                abs_path = os.path.join(root, filename)
                rel_path = os.path.relpath(abs_path, skill_dir)
                files.append(rel_path)
        files.sort()

        _registry[name] = SkillInfo(
            name=name,
            description=description,
            skill_dir=skill_dir,
            files=files,
        )
        logger.info("已加载 Skill: %s (%s)", name, description[:50])

    logger.info("Skill 索引构建完成，共 %d 个 Skill", len(_registry))


def get_all_skills() -> list[SkillInfo]:
    """返回全量 Skill 列表。"""
    return list(_registry.values())


def get_skill(name: str) -> Optional[SkillInfo]:
    """按名称查询单个 Skill。"""
    return _registry.get(name)


def is_valid_skill(name: str) -> bool:
    """检查 Skill 名称是否存在于全局索引。"""
    return name in _registry


def load_skill_content(name: str) -> Optional[str]:
    """加载指定 Skill 的 SKILL.md 完整文本内容。"""
    info = _registry.get(name)
    if info is None:
        return None
    skill_md_path = os.path.join(info.skill_dir, _SKILL_MD)
    try:
        with open(skill_md_path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        logger.error("读取 Skill '%s' 的 %s 失败: %s", name, _SKILL_MD, e)
        return None


def load_skill_files(name: str) -> Optional[list[str]]:
    """返回指定 Skill 目录下的相对文件路径列表。"""
    info = _registry.get(name)
    if info is None:
        return None
    return info.files


def _parse_skill_md(path: str) -> tuple[Optional[str], str]:
    """解析 SKILL.md 的 YAML front-matter，返回 (name, description)。

    front-matter 格式::

        ---
        name: frontend-design
        description: Create distinctive...
        ---

    如果缺少 name，返回 (None, "")。
    如果缺少 description，默认为空字符串。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None, ""

    if not content.startswith("---"):
        return None, ""

    # 找到 front-matter 结束标记
    end_marker = content.find("---", 3)
    if end_marker == -1:
        return None, ""

    front_matter = content[3:end_marker].strip()

    # 简单的 YAML 解析（避免引入额外依赖）
    # TODO: 支持多行 description（当前逐行匹配仅支持单行值）
    name = None
    description = ""
    for line in front_matter.split("\n"):
        line = line.strip()
        if line.startswith("name:"):
            name = line[5:].strip()
        elif line.startswith("description:"):
            description = line[12:].strip()

    return name, description