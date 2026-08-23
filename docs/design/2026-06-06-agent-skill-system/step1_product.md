# V23: Skill 系统 - 产品文档

## 目标

为 Agent 引入"技能（Skill）"机制，让每个 Team 可以为其 Agent 选择性地授权和加载预置技能包。Skill 以目录形式组织，内含 SKILL.md 描述文件和若干资源文件；Agent 通过 `load_skill` 工具按需加载已授权的 Skill，加载后 Skill 的核心内容注入 Agent 上下文，使 Agent 具备对应领域的专业能力。

此前的 Agent 能力完全由角色模板的 prompt 和工具列表决定，缺乏按场景动态扩展知识的途径。V23 之后，管理员可以为 Agent 授权一组 Skill，Agent 在需要时可主动加载 Skill 内容，获得领域知识、操作规范和参考资源，而无需修改角色模板或重启服务。

---

## 功能特性

### 一、Skill 资源管理

- **Skill 目录结构**：每个 Skill 以独立目录形式存放在 `assets/skills/` 下，目录名即为 Skill 名称。目录内必须包含 `SKILL.md` 文件作为 Skill 元数据和核心内容入口。
- **SKILL.md 格式**：采用 YAML front-matter + Markdown 正文格式。front-matter 中声明 `name`（Skill 名称）和 `description`（一句话描述），正文为 Skill 的核心指令内容，加载后将注入到 Agent 上下文中。
- **启动扫描**：服务启动时自动扫描 `assets/skills/` 目录，解析每个子目录下的 `SKILL.md`，构建 Skill 索引并缓存。无效或缺失 `SKILL.md` 的目录会被跳过并记录警告日志。
- **Skill 查询接口**：后端提供 `GET /config/skills/list.json` 接口，返回服务上所有可用 Skill 的名称和描述列表，供前端展示和配置使用。

### 二、Agent 授权机制

- **白名单授权**：每个 Agent 新增 `allow_skills` 字段（类型为 `list[str] | None`，存入数据库时为 JSON 数组或 NULL），表示该 Agent 被授权使用的 Skill 列表。只有出现在白名单中的 Skill，Agent 才能通过 `load_skill` 工具加载。
- **默认无授权**：`allow_skills` 默认为 `None`（或空列表），表示该 Agent 当前无任何 Skill 授权，调用 `load_skill` 将返回权限错误。
- **前端配置**：Web Console 的 Agent 编辑对话框中新增 Skill 授权区域，管理员可从可用 Skill 列表中勾选授权给该 Agent 的 Skill。
- **API 持久化**：Agent 保存接口（创建/更新）支持传入 `allow_skills` 字段，授权列表持久化到数据库 `agents` 表。

### 三、Prompt 注入

- **系统级提示**：当 Agent 的 `allow_skills` 非空时，在其系统提示（system prompt）末尾自动追加一段 Skill 概要信息，格式为已授权 Skill 的名称和描述列表，引导 Agent 在需要时主动调用 `load_skill` 工具。
- **加载时注入**：Agent 调用 `load_skill` 成功后，Skill 的核心内容（SKILL.md 正文）作为工具返回值的一部分返回给 Agent，Agent 可据此在后续对话中使用该 Skill 的知识。

### 四、load_skill 工具

- **工具注册**：新增 `load_skill` 工具函数，注册到 `FUNCTION_REGISTRY`，归属于 BASIC 工具类别，所有 Agent 均可调用。
- **参数**：`skill_name: str` — 要加载的 Skill 名称。
- **权限校验**：从 `_context.agent_id` 查询 Agent 的 `allow_skills` 列表，若 `skill_name` 不在授权列表中则返回权限错误；若 `allow_skills` 为 None 或空列表，同样返回权限错误。
- **返回值**：包含 `success`、`skill_name`、`description`、`content`（SKILL.md 正文内容）和 `files`（Skill 目录下其他资源文件列表及可选内容）。

### 五、前端改动

- **Agent 编辑对话框**：在 Agent 编辑/创建对话框中增加"技能授权"区域，以多选列表形式展示所有可用 Skill，管理员勾选后保存为 `allow_skills`。
- **Skill 概要展示**：Agent 详情页面展示该 Agent 已授权的 Skill 列表（名称 + 描述）。

### 六、配置持久化与导入导出

- **Team 配置导入**：`AgentConfig` 新增 `allow_skills: List[str] | None = None` 字段，Team 配置 JSON 中可为每个 Agent 指定授权 Skill 列表。
- **导出**：导出 Team 配置时包含每个 Agent 的 `allow_skills` 字段。
- **数据库迁移**：新增迁移脚本 `0012_add_allow_skills_to_agents.sql`，为 `agents` 表添加 `allow_skills TEXT` 列。

---

## 用户价值

1. **按需赋能**：Agent 无需在创建时就具备所有知识，而是在需要时动态加载所需的领域技能。
2. **灵活授权**：管理员可精确控制每个 Agent 可以使用哪些 Skill，避免越权。
3. **易于扩展**：新增 Skill 只需在 `assets/skills/` 下添加目录和 `SKILL.md`，无需修改代码或重启服务（重启后自动索引）。

---

## 典型场景

- **代码审查**：为 Agent 授权"代码审查"Skill，Agent 在审查代码时加载该 Skill，获得审查规范和常见问题清单。
- **测试设计**：为 Agent 授权"测试用例设计"Skill，Agent 在编写测试时加载该 Skill，获得测试方法论和模板。
- **文档编写**：为 Agent 授权"技术文档撰写"Skill，Agent 根据加载的写作规范和模板输出更规范的技术文档。

---

## 产品边界

- V23 仅支持服务预置的 Skill（`assets/skills/` 目录下的 Skill），不支持用户自定义上传 Skill。
- Skill 加载后内容仅作为工具返回值，不会自动修改 Agent 的 system prompt（除概要注入外）。
- Skill 不涉及工具函数注册，仅提供知识型内容。
- 前端暂不提供 Skill 内容的在线预览和编辑，需通过文件系统直接管理。

---

## 验收标准

1. 服务启动后能自动扫描 `assets/skills/` 并索引有效 Skill。
2. `GET /config/skills/list.json` 返回正确的 Skill 列表。
3. `load_skill` 工具在 Agent 已授权时返回 Skill 内容，未授权时返回权限错误。
4. Agent 的 `allow_skills` 字段可通过 API 创建/更新，并持久化到数据库。
5. 已授权 Agent 的 system prompt 末尾包含 Skill 概要引导信息。
6. Team 配置导入导出正确处理 `allow_skills` 字段。
7. 前端 Agent 编辑对话框可配置 Skill 授权并生效。