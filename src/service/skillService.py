"""Skill 服务：扫描、索引、查询、加载 Skill 资源。"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import yaml
import appPaths

logger = logging.getLogger(__name__)

_SKILL_MD = "SKILL.md"


@dataclass
class SkillInfo:
    """Skill 的索引信息，启动时扫描生成。"""
    name: str
    description: str
    skill_dir: str
    is_builtin: bool = True
    content: str = ""
    files: list[str] = field(default_factory=list)


_registry: dict[str, SkillInfo] = {}


def _scan_skills_in_dir(scan_dir: str, is_builtin: bool) -> None:
    if not os.path.isdir(scan_dir):
        logger.info("Skill 目录不存在，跳过扫描: %s", scan_dir)
        return

    for entry in os.listdir(scan_dir):
        skill_dir = os.path.join(scan_dir, entry)
        if not os.path.isdir(skill_dir):
            continue

        skill_info = load_skill_from_disk(skill_dir, is_builtin=is_builtin)
        if skill_info:
            if skill_info.name in _registry:
                logger.info("覆盖同名 Skill: %s (原 is_builtin=%s, 新 is_builtin=%s)", 
                            skill_info.name, _registry[skill_info.name].is_builtin, is_builtin)
            _registry[skill_info.name] = skill_info
            logger.info("已加载 Skill: %s (%s) [builtin=%s]", skill_info.name, skill_info.description[:50], is_builtin)

def startup() -> None:
    """扫描 assets/skills/ 目录以及 storage_root 的 skills 目录，构建全局 Skill 索引。"""
    global _registry
    _registry = {}

    try:
        os.makedirs(appPaths.USER_SKILLS_DIR, exist_ok=True)
        if "PYTEST_CURRENT_TEST" not in os.environ:
            from util.configUtil import sync_file_if_changed
            sync_file_if_changed("docs/skills.README.md", appPaths.USER_SKILLS_DIR, "README.md")
    except OSError as e:
        logger.warning("无法创建 USER_SKILLS_DIR: %s", e)

    _scan_skills_in_dir(appPaths.BUILTIN_SKILLS_DIR, is_builtin=True)
    _scan_skills_in_dir(appPaths.USER_SKILLS_DIR, is_builtin=False)

    logger.info("Skill 索引构建完成，共 %d 个 Skill", len(_registry))


def load_skill_from_disk(skill_dir: str, is_builtin: bool = True) -> Optional[SkillInfo]:
    """从本地目录加载并解析一个 Skill。"""
    entry = os.path.basename(skill_dir)
    skill_md_path = os.path.join(skill_dir, _SKILL_MD)
    if not os.path.isfile(skill_md_path):
        logger.warning("Skill 目录 '%s' 缺少 %s，跳过", entry, _SKILL_MD)
        return None

    name, description, content = _parse_skill_md(skill_md_path)
    if name is None:
        logger.warning("Skill '%s' 的 %s 缺少有效的 front-matter，跳过", entry, _SKILL_MD)
        return None

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

    return SkillInfo(
        name=name,
        description=description,
        content=content,
        skill_dir=skill_dir,
        is_builtin=is_builtin,
        files=files,
    )

def shutdown() -> None:
    """清理 Skill 索引。"""
    global _registry
    _registry.clear()
    logger.info("Skill 服务已关闭")


def get_all_skills() -> list[SkillInfo]:
    """返回全量 Skill 列表。"""
    return list(_registry.values())


def get_skill(name: str) -> Optional[SkillInfo]:
    """按名称查询单个 Skill。"""
    return _registry.get(name)


def is_valid_skill(name: str) -> bool:
    """检查 Skill 名称是否存在于全局索引。"""
    return name in _registry





def _parse_skill_md(path: str) -> tuple[Optional[str], str, str]:
    """解析 SKILL.md 的 YAML front-matter，返回 (name, description, content)。

    front-matter 格式::

        ---
        name: frontend-design
        description: Create distinctive...
        ---

    如果缺少 name，返回 (None, "", "")。
    如果缺少 description，默认为空字符串。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return None, "", ""

    if not content.startswith("---"):
        return None, "", content

    # 找到 front-matter 结束标记
    end_marker = content.find("---", 3)
    if end_marker == -1:
        return None, "", content

    front_matter = content[3:end_marker].strip()
    
    try:
        parsed = yaml.safe_load(front_matter) or {}
        name = parsed.get("name")
        description = parsed.get("description", "")
        
        if name is not None:
            name = str(name).strip()
        if description is not None:
            description = str(description).strip()
            
    except Exception as e:
        logger.error("解析 YAML front-matter 失败: %s", e)
        return None, "", content

    return name, description, content