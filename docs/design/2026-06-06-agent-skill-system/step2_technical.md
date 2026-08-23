# V23: Skill 系统 - 技术文档

## 1. 方案概览

### 1.1 目标

为 Agent 引入 Skill 机制，每个 Agent 可被授权使用若干预置技能包。Skill 以目录+SKILL.md 形式组织，Agent 通过 `load_skill` 工具按需加载，加载后 Skill 内容注入 Agent 上下文。

### 1.2 核心挑战

| 编号 | 问题 | 难度 |
|------|------|------|
| ① | Skill 索引与扫描：需在服务启动时扫描目录、解析 YAML front-matter，构建缓存 | 简单 |
| ② | 权限校验：`load_skill` 需从运行态或数据库查询 Agent 的 `allow_skills`，并发环境下需保证一致性 | 中等 |
| ③ | Prompt 注入：需在 Agent system prompt 构建流程中新增 Skill 概要注入，且不破坏现有 prompt 结构 | 简单 |
| ④ | 前端联动：Agent 编辑对话框需新增 Skill 多选，前端需调用 Skill 列表 API | 中等 |
| ⑤ | 配置导入导出：Team 配置需同步处理 `allow_skills` 字段 | 简单 |

### 1.3 改动范围概览

| 层 | 改动内容 |
|----|---------|
| **数据模型** | `GtAgent` 新增 `allow_skills: list[str] \| None` 字段，使用 `JsonFieldWithClass(list[str], null=True)` |
| **数据库迁移** | 新增 `0012_add_allow_skills_to_agents.sql`：`ALTER TABLE agents ADD COLUMN allow_skills TEXT` |
| **Skill 索引服务** | 新增 `skillService.py`：启动扫描、Skill 查询、内容加载 |
| **工具注册** | `funcToolService/tools.py` 新增 `load_skill` 函数，`funcToolService/core.py` 注册 |
| **工具分类** | `toolRegistry.py` 中 `load_skill` 归属 `BASIC` 类别 |
| **Prompt 构建** | `promptBuilder.py` 的 `build_agent_system_prompt` 新增 `allow_skills` 参数 |
| **Agent 服务** | `agentService/core.py` 调用 `build_agent_system_prompt` 时传入 `gt_agent.allow_skills` |
| **配置类型** | `configTypes.py` 的 `AgentPreset` 新增 `allow_skills: List[str] \| None = None` |
| **配置加载** | `configUtil.py` 迁移 `reserve_output_tokens == 8192` → 16384（本轮一并处理） |
| **导入导出** | `presetService.py` 导入时处理 `allow_skills`；导出时包含 `allow_skills` |
| **保存接口** | `agentController.py` 的 `AgentSaveItem` 新增 `allow_skills` 字段，保存流程写入 |
| **后端接口** | `settingController.py` 新增 `SkillListHandler`（GET `/config/skills/list.json`） |
| **路由** | `route.py` 新增 `/config/skills/list.json` 路由 |
| **前端** | Agent 编辑对话框新增 Skill 授权多选区域；Agent 详情展示已授权 Skill |

---

## 2. 数据模型

### 2.1 GtAgent 新增字段

```python
# src/model/dbModel/gtAgent.py
from .base import DbModelBase, EnumField, JsonField, JsonFieldWithClass

class GtAgent(DbModelBase):
    # ... 现有字段 ...
    allow_skills: list[str] | None = JsonFieldWithClass(list[str], null=True)
```

- 数据库列类型：`TEXT`（JSON 序列化存储）
- 存储格式：`["skill_a", "skill_b"]` 或 `NULL`
- 读取时通过 `JsonFieldWithClass` 自动反序列化为 `list[str]`

### 2.2 数据库迁移

```sql
-- assets/migrate/0012_add_allow_skills_to_agents.sql
ALTER TABLE agents ADD COLUMN allow_skills TEXT;
```

### 2.3 SkillInfo 数据类

```python
# src/service/skillService.py
from dataclasses import dataclass

@dataclass
class SkillInfo:
    name: str           # Skill 名称（目录名）
    description: str    # 一句话描述（来自 SKILL.md front-matter）
    skill_dir: str       # Skill 目录绝对路径
    files: list[str]     # Skill 目录下除 SKILL.md 外的其他文件名列表
```

---

## 3. SkillService 服务

### 3.1 职责

- 服务启动时扫描 `assets/skills/` 目录
- 解析每个子目录的 `SKILL.md`，提取 front-matter（YAML）中的 `name` 和 `description`
- 建立 Skill 名称 → SkillInfo 的内存索引
- 提供查询接口：`get_all_skills()`、`get_skill(name)`、`is_valid_skill(name)`、`load_skill_content(name)`、`load_skill_files(name)`

### 3.2 SKILL.md 格式

```markdown
---
name: code_review
description: 代码审查技能包，提供审查规范和常见问题清单
---

# 代码审查技能

## 审查规范

1. 检查命名一致性
2. 检查错误处理完整性
...
```

- front-matter 使用 YAML 格式，必须包含 `name` 和 `description`
- 正文为 Markdown 格式，作为 Skill 的核心内容

### 3.3 _parse_skill_md 实现

```python
def _parse_skill_md(skill_dir: str, skill_name: str) -> SkillInfo | None:
    """解析 SKILL.md 文件，返回 SkillInfo 或 None（解析失败时）。"""
    md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(md_path):
        logger.warning(f"Skill '{skill_name}' 缺少 SKILL.md，已跳过")
        return None

    with open(md_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 解析 YAML front-matter
    if not content.startswith("---"):
        logger.warning(f"Skill '{skill_name}' 的 SKILL.md 缺少 front-matter，已跳过")
        return None

    end = content.find("---", 3)
    if end == -1:
        logger.warning(f"Skill '{skill_name}' 的 SKILL.md front-matter 未闭合，已跳过")
        return None

    yaml_str = content[3:end].strip()
    try:
        meta = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        logger.warning(f"Skill '{skill_name}' 的 SKILL.md YAML 解析失败: {e}")
        return None

    if not isinstance(meta, dict) or "name" not in meta or "description" not in meta:
        logger.warning(f"Skill '{skill_name}' 的 SKILL.md 缺少 name 或 description 字段，已跳过")
        return None

    # 收集其他文件
    files = [
        f for f in os.listdir(skill_dir)
        if f != "SKILL.md" and os.path.isfile(os.path.join(skill_dir, f))
    ]

    return SkillInfo(
        name=meta["name"],
        description=meta["description"],
        skill_dir=skill_dir,
        files=sorted(files),
    )
```

### 3.4 startup 流程

```python
_skills: dict[str, SkillInfo] = {}

async def startup() -> None:
    global _skills
    skills_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "skills")
    if not os.path.isdir(skills_dir):
        logger.info(f"Skill 目录不存在，跳过扫描: {skills_dir}")
        return

    _skills = {}
    for entry in os.listdir(skills_dir):
        entry_path = os.path.join(skills_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        info = _parse_skill_md(entry_path, entry)
        if info is not None:
            _skills[info.name] = info
            logger.info(f"Skill 已索引: {info.name} - {info.description}")

    logger.info(f"Skill 索引完成，共 {len(_skills)} 个有效 Skill")
```

---

## 4. load_skill 工具

### 4.1 工具定义

```python
async def load_skill(skill_name: str, _context: ToolCallContext = None) -> dict:
    """加载指定名称的技能包。需要 Agent 已被授权使用该技能。

    Args:
        skill_name: 技能名称
    """
```

### 4.2 权限校验流程

```
1. 从 _context.agent_id 获取当前 Agent ID
2. 从数据库查询 Agent 的 allow_skills 字段
3. 若 allow_skills 为 None 或空列表 → 返回权限错误
4. 若 skill_name 不在 allow_skills 中 → 返回权限错误
5. 权限通过 → 调用 skillService 加载 Skill 内容
```

### 4.3 返回值结构

```json
{
    "success": true,
    "skill_name": "code_review",
    "description": "代码审查技能包",
    "content": "# 代码审查技能\n\n## 审查规范\n...",
    "files": ["checklist.md", "examples.py"]
}
```

权限错误时：
```json
{
    "success": false,
    "message": "你没有被授权使用技能 'code_review'。请联系管理员在 Agent 配置中为你授权该技能。"
}
```

---

## 5. Prompt 注入

### 5.1 build_agent_system_prompt 改动

```python
async def build_agent_system_prompt(
    team_id: int,
    agent_name: str,
    template_name: str,
    template_soul: str,
    workdir: str,
    base_prompt_tmpl: str,
    identity_prompt_tmpl: str,
    allow_skills: list[str] | None = None,  # 新增参数
) -> str:
    # ... 现有逻辑 ...

    # 注入 Skill 概要
    if allow_skills:
        from service import skillService
        skill_summaries = []
        for skill_name in allow_skills:
            info = skillService.get_skill(skill_name)
            if info is not None:
                skill_summaries.append(f"- {info.name}: {info.description}")
        if skill_summaries:
            skill_prompt = (
                "\n\n---\n\n"
                "你已被授权使用以下技能（Skill），在需要时可以调用 load_skill 工具加载对应技能的详细内容：\n\n"
                + "\n".join(skill_summaries)
            )
            full_prompt += skill_prompt

    return full_prompt
```

### 5.2 agentService/core.py 调用改动

```python
# 在 _load_team_agents 中
full_prompt = await build_agent_system_prompt(
    team_id=team_id,
    agent_name=agent_name,
    template_name=template_name,
    template_soul=gt_role_template.soul,
    workdir=team_workdir,
    base_prompt_tmpl=BASE_PROMPT.strip(),
    identity_prompt_tmpl=AGENT_IDENTITY_PROMPT.strip(),
    allow_skills=gt_agent.allow_skills,  # 新增参数
)
```

---

## 6. 后端接口

### 6.1 Skill 列表接口

```python
class SkillListHandler(BaseHandler):
    """GET /config/skills/list.json - 返回所有可用 Skill 列表"""

    async def get(self):
        from service import skillService
        skills = skillService.get_all_skills()
        self.return_json({
            "skills": [
                {"name": info.name, "description": info.description}
                for info in skills.values()
            ]
        })
```

### 6.2 Agent 保存接口改动

`AgentSaveItem` 新增字段：

```python
class AgentSaveItem(BaseModel):
    id: Optional[int] = None
    name: str
    role_template_id: int
    model: str = ""
    driver: DriverType = DriverType.NATIVE
    allow_skills: list[str] | None = None  # 新增
```

保存时将 `allow_skills` 写入 `GtAgent`。

---

## 7. 前端改动

### 7.1 Agent 编辑对话框

在 Agent 编辑/创建对话框中：

1. 新增"技能授权"区域
2. 调用 `GET /config/skills/list.json` 获取可用 Skill 列表
3. 以多选 Checkbox 形式展示，已授权的 Skill 默认勾选
4. 保存时将勾选结果作为 `allow_skills` 数组提交

### 7.2 Agent 详情页

在 Agent 详情页增加已授权 Skill 列表展示（名称 + 描述）。

---

## 8. 配置持久化

### 8.1 AgentConfig 新增字段

```python
# src/util/configTypes.py
class AgentConfig(BaseModel):
    name: str
    i18n: I18nData | None = None
    role_template: str
    model: Optional[str] = None
    driver: DriverType = DriverType.TSP
    allow_skills: List[str] | None = None  # 新增
```

### 8.2 导入流程

`presetService.py` 导入 Team 配置时，将 `AgentConfig.allow_skills` 写入 `GtAgent.allow_skills`。

### 8.3 导出流程

导出 Team 配置时，将 `GtAgent.allow_skills` 序列化为 `AgentConfig.allow_skills`。

---

## 9. 改动文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `src/model/dbModel/gtAgent.py` | 修改 | 新增 `allow_skills` 字段 |
| `src/model/dbModel/base.py` | 修改 | `JsonFieldWithClass` 支持泛型类型 |
| `src/service/skillService.py` | 新增 | Skill 扫描、索引、查询服务 |
| `src/service/funcToolService/tools.py` | 修改 | 新增 `load_skill` 工具函数 |
| `src/service/funcToolService/core.py` | 修改 | 注册 `load_skill` 工具 |
| `src/service/agentService/toolRegistry.py` | 修改 | `load_skill` 归属 BASIC 类别 |
| `src/service/agentService/promptBuilder.py` | 修改 | 新增 `allow_skills` 参数及注入逻辑 |
| `src/service/agentService/core.py` | 修改 | 传入 `gt_agent.allow_skills` |
| `src/controller/settingController.py` | 修改 | 新增 `SkillListHandler` |
| `src/controller/agentController.py` | 修改 | `AgentSaveItem` 新增 `allow_skills`，保存流程 |
| `src/route.py` | 修改 | 新增 Skill 列表路由 |
| `src/util/configTypes.py` | 修改 | `AgentPreset` 新增 `allow_skills` |
| `src/service/presetService.py` | 修改 | 导入时处理 `allow_skills` |
| `src/util/configUtil.py` | 修改 | 迁移 `reserve_output_tokens == 8192` → 16384 |
| `assets/migrate/0012_add_allow_skills_to_agents.sql` | 新增 | 数据库迁移脚本 |
| 前端 `ModelServiceEditorDialog.vue` | 修改 | 输出 token 兜底值 8192 → 16384 |
| 前端 Agent 编辑对话框 | 修改 | 新增 Skill 授权多选 |

---

## 10. 测试要点

1. **Skill 扫描**：在 `assets/skills/` 下放置有效的 Skill 目录（含 SKILL.md），验证启动后索引正确；放置无效目录（缺少 SKILL.md、格式错误），验证被跳过且不报错。
2. **Skill 列表接口**：验证 `GET /config/skills/list.json` 返回正确的 Skill 名称和描述列表。
3. **load_skill 权限**：验证未授权 Agent 调用返回权限错误；已授权 Agent 调用返回正确内容。
4. **load_skill 内容**：验证返回的 `content` 为 SKILL.md 正文（不含 front-matter），`files` 为目录下其他文件列表。
5. **Prompt 注入**：验证 `allow_skills` 非空时 system prompt 末尾包含 Skill 概要；为空或 None 时无额外注入。
6. **配置导入导出**：验证 Team 配置 JSON 中的 `allow_skills` 字段能正确导入和导出。
7. **前端配置**：验证 Agent 编辑对话框的 Skill 多选可正确保存和回显。
8. **数据库迁移**：验证迁移脚本正确添加 `allow_skills` 列，已有数据不受影响。