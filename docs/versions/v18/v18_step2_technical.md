# V18: 国际化（i18n）支持 - 技术文档

## 1. 方案概览

### 1.1 目标

为系统全部用户可见的界面文案和内置预置内容提供中英双语支持，语言偏好存储在后端配置中，多端共享。

### 1.2 核心挑战

| 编号 | 问题 | 难度 |
|------|------|------|
| ① | Web 前端没有 i18n 框架，约 20+ 组件中的中文硬编码需全量抽取 | 工作量大 |
| ② | Preset 文件当前是纯中文 JSON，需设计内嵌多语言格式并兼容现有导入流程 | 中等 |
| ③ | DB 实体（Team/Agent/Room/RoleTemplate）没有多语言字段，需新增 `i18n` 字段并改造展示逻辑 | 中等 |
| ④ | macOS 托盘菜单（pystray）和 TUI（Textual）没有现成 i18n 方案，需自建轻量翻译层 | 简单 |
| ⑤ | 语言偏好需跨 Web / TUI / macOS 托盘同步，且变更后各端即时生效 | 中等 |

### 1.3 改动范围概览

| 层 | 改动内容 |
|----|---------|
| **Preset 文件** | `assets/preset/` 下的 role_templates/*.json 和 teams/*.json 改为内嵌多语言格式 |
| **配置层** | `SettingConfig` 新增 `language` 字段；`configUtil` 新增语言读写方法 |
| **数据模型** | `GtTeam`、`GtAgent`、`GtRoom`、`GtRoleTemplate` 新增 `i18n` JsonField |
| **Preset 导入** | `presetService` / `configTypes` 适配新 Preset 格式，导入时写入 i18n 字段 |
| **后端 API** | 新增语言读取/切换接口；现有列表 API 返回数据追加 `display_name` |
| **Web 前端** | 引入 `vue-i18n`，抽取全量翻译 key，新增语言切换 UI |
| **TUI** | 自建翻译字典，启动时从后端读取语言配置 |
| **macOS 托盘** | 菜单项文案查翻译字典，新增语言切换菜单项 |

---

## 2. 语言配置存储

### 2.1 存储位置

语言偏好存储在 `~/.togo_agent/setting.json` 的 `SettingConfig` 中，与 LLM 服务配置同级：

```python
# src/util/configTypes.py
class SettingConfig(BaseModel):
    language: str = "zh-CN"   # 新增：当前语言，默认中文
    # ... 现有字段不变
```

### 2.2 支持的语言代码

| 代码 | 语言 |
|------|------|
| `zh-CN` | 中文（默认） |
| `en` | 英文 |

### 2.3 读写接口

```python
# src/util/configUtil.py

def get_language() -> str:
    """获取当前语言设置。"""
    return get_app_config().setting.language

def set_language(lang: str) -> None:
    """修改语言设置并持久化到 setting.json。"""
    update_setting(lambda s: setattr(s, 'language', lang))
```

`update_setting` 已有原子写回机制（先写 tmp 再 `os.replace`），无需额外处理。

### 2.4 setting.json 写回

`_save_setting_to_file()` 需新增 `language` 字段的写回：

```python
raw["language"] = setting.language
```

---

## 3. Preset 文件格式改造

### 3.1 RoleTemplate preset

**改造前：**

```json
{
  "name": "researcher",
  "soul": "你是一名研究员..."
}
```

**改造后：**

```json
{
  "name": "researcher",
  "i18n": {
    "display_name": {"zh-CN": "研究员", "en": "Researcher"}
  },
  "soul": "你是一名研究员...",
  "allowed_tools": ["Read"]
}
```

- `name`：稳定标识符，不翻译，Team 中通过此 name 引用
- `i18n.display_name`：多语言展示名，浏览模板列表时根据当前语言显示
- `soul`：LLM prompt，不翻译

### 3.2 Team preset

**改造前：**

```json
{
  "name": "默认团队",
  "agents": [
    {"name": "王老师", "role_template": "researcher"}
  ],
  "preset_rooms": [
    {"name": "王老师", "agents": ["Operator", "王老师"]}
  ],
  "dept_tree": {
    "dept_name": "总部",
    "manager": "小马哥",
    "agents": ["小马哥", "王老师"]
  }
}
```

**改造后：**

```json
{
  "uuid": "550e8400-e29b-41d4-a716-446655440000",
  "name": "default",
  "i18n": {
    "display_name": {"zh-CN": "默认团队", "en": "Default Team"}
  },
  "agents": [
    {
      "name": "wang_teacher",
      "i18n": {
        "display_name": {"zh-CN": "王老师", "en": "Prof. Wang"}
      },
      "role_template": "researcher"
    }
  ],
  "preset_rooms": [
    {
      "i18n": {
        "display_name": {"zh-CN": "王老师", "en": "Prof. Wang"},
        "initial_topic": {
          "zh-CN": "你好，我是人类操作者。你可以根据我的指令执行任务。",
          "en": "Hello, I'm the human operator. You can execute tasks according to my instructions."
        }
      },
      "agents": ["Operator", "wang_teacher"],
      "max_turns": 100
    }
  ],
  "dept_tree": {
    "i18n": {
      "dept_name": {"zh-CN": "总部", "en": "Headquarters"},
      "responsibility": {"zh-CN": "负责整体运营与协作", "en": "Overall operations and collaboration"}
    },
    "manager": "manager_ma",
    "agents": ["manager_ma", "wang_teacher"]
  }
}
```

关键变更：

| 字段 | 变更 | 说明 |
|------|------|------|
| `uuid` | 新增 | Team 唯一标识，防重复导入 |
| `name` | 从展示名改为稳定标识符 | 如 `"default"` 而非 `"默认团队"` |
| `i18n` | 新增 | 统一容器，包含 `display_name` 等多语言字段 |
| `i18n.display_name` | 新增 | `{"zh-CN": "...", "en": "..."}` 格式 |
| `agents[].name` | 从展示名改为稳定标识符 | 如 `"wang_teacher"` 而非 `"王老师"` |
| `agents[].i18n` | 新增 | 每个 Agent 的多语言数据 |
| 跨引用字段 | 使用稳定 name | `preset_rooms[].agents[]`、`dept_tree.manager/agents` |

### 3.3 configTypes 改造

```python
# 多语言字段类型
I18nText = dict[str, str]  # e.g. {"zh-CN": "研究员", "en": "Researcher"}
I18nData = dict[str, I18nText]  # e.g. {"display_name": {"zh-CN": "研究员", "en": "Researcher"}}

class RoleTemplateConfig(BaseModel):
    name: str
    i18n: I18nData | None = None             # 新增
    soul: str = ""
    prompt_file: str = ""
    model: Optional[str] = None
    allowed_tools: List[str] | None = None

class AgentConfig(BaseModel):
    name: str
    i18n: I18nData | None = None             # 新增
    role_template: str
    model: Optional[str] = None
    driver: DriverType = DriverType.TSP

class TeamRoomConfig(BaseModel):
    id: Optional[int] = None
    name: str = ""
    i18n: I18nData | None = None             # 新增：含 display_name, initial_topic
    agents: List[str]
    initial_topic: str = ""                  # 保留旧格式兼容
    max_turns: int | None = None
    biz_id: str | None = None
    tags: List[str] = Field(default_factory=list)

class DeptNodeConfig(BaseModel):
    dept_name: str = ""                      # 保留旧格式兼容
    i18n: I18nData | None = None             # 新增：含 dept_name, responsibility
    responsibility: str = ""
    manager: str
    agents: List[str] = Field(default_factory=list)
    children: List["DeptNodeConfig"] = Field(default_factory=list)

class TeamConfig(BaseModel):
    uuid: str | None = None                  # 新增
    name: str
    i18n: I18nData | None = None             # 新增
    config: dict[str, Any] = Field(default_factory=dict)
    agents: List[AgentConfig] = Field(default_factory=list)
    dept_tree: Optional[DeptNodeConfig] = None
    preset_rooms: List[TeamRoomConfig] = Field(default_factory=list)
```

### 3.4 兼容性

- 所有新增 `i18n` 字段均为 `Optional`（默认 None），旧格式 JSON 可正常解析
- `I18nData` 为 `dict[str, I18nText]` 别名，Pydantic 自动校验
- 旧字段（`initial_topic: str`、`dept_name: str`）保留以兼容旧格式，导入逻辑优先从 `i18n` 中读取
- Preset JSON 中 `i18n` 字段的结构与 DB 模型的 `i18n` JsonField 一一对应，导入时可直接映射

---

## 4. 数据库模型改造

### 4.1 新增 i18n 字段

以下模型新增 `i18n` JsonField：

```python
# GtTeam
class GtTeam(DbModelBase):
    name: str = peewee.CharField(unique=True)
    uuid: str | None = peewee.CharField(null=True, unique=True)  # 新增
    i18n: dict = JsonField(default=dict)                          # 新增
    # ... 其他字段不变

# GtAgent
class GtAgent(DbModelBase):
    i18n: dict = JsonField(default=dict)                          # 新增
    # ... 其他字段不变

# GtRoom
class GtRoom(DbModelBase):
    i18n: dict = JsonField(default=dict)                          # 新增
    # ... 其他字段不变

# GtRoleTemplate
class GtRoleTemplate(DbModelBase):
    i18n: dict = JsonField(default=dict)                          # 新增
    # ... 其他字段不变
```

### 4.2 i18n 字段格式

```json
{
  "display_name": {"zh-CN": "默认团队", "en": "Default Team"}
}
```

不同实体可包含不同的 i18n key：

| 实体 | i18n 中可能的 key |
|------|-------------------|
| `GtTeam` | `display_name` |
| `GtAgent` | `display_name` |
| `GtRoom` | `display_name`, `initial_topic` |
| `GtRoleTemplate` | `display_name` |

### 4.3 展示名解析工具

```python
# src/util/i18nUtil.py

from util import configUtil

DEFAULT_LANG = "zh-CN"

def resolve_display_name(entity_name: str, i18n: dict | None) -> str:
    """从 i18n 数据中解析当前语言的展示名。

    优先级：i18n.display_name[当前语言] → i18n.display_name[默认语言] → entity_name
    """
    if not i18n:
        return entity_name
    display_names = i18n.get("display_name")
    if not display_names or not isinstance(display_names, dict):
        return entity_name
    lang = configUtil.get_language()
    return display_names.get(lang) or display_names.get(DEFAULT_LANG) or entity_name
```

### 4.4 数据库迁移

使用 peewee 的 `Migrator` 添加字段：

```python
migrator.add_column('teams', 'uuid', peewee.CharField(null=True, unique=True))
migrator.add_column('teams', 'i18n', JsonField(default=dict))
migrator.add_column('agents', 'i18n', JsonField(default=dict))
migrator.add_column('rooms', 'i18n', JsonField(default=dict))
migrator.add_column('role_templates', 'i18n', JsonField(default=dict))
```

已有数据的 `i18n` 默认为空 dict `{}`，`resolve_display_name` 会回退到 `entity.name`。

---

## 5. Preset 导入改造

### 5.1 RoleTemplate 导入

`presetService._import_role_templates_from_app_config()` 改造：

```python
await roleTemplateService.save_role_template(GtRoleTemplate(
    name=template.name,
    soul=template.soul,
    model=template.model,
    type=RoleTemplateType.SYSTEM,
    allowed_tools=template.allowed_tools,
    i18n=template.i18n or {},  # 新增：直接映射 preset → DB
))
```

### 5.2 Team 导入去重

`presetService._import_team_from_config()` 改造：

```python
async def _import_team_from_config(team_config: TeamConfig) -> GtTeam | None:
    # 优先按 UUID 去重
    if team_config.uuid:
        existing = await gtTeamManager.get_team_by_uuid(team_config.uuid)
        if existing is not None:
            logger.info("Team UUID '%s' 已存在，跳过导入", team_config.uuid)
            return None
    else:
        # 旧格式回退到 name 去重
        existing = await gtTeamManager.get_team(team_config.name)
        if existing is not None:
            logger.info("Team '%s' 已存在，跳过导入", team_config.name)
            return None

    # 实例化时用当前语言的 display_name 作为 DB name
    lang = configUtil.get_language()
    i18n = team_config.i18n or {}
    db_name = _resolve_i18n_text(i18n.get("display_name"), lang) or team_config.name

    team = await gtTeamManager.save_team(GtTeam(
        name=db_name,
        uuid=team_config.uuid,
        i18n=i18n,                            # 直接映射 preset → DB
        config=team_config.config or {},
        enabled=1,
        deleted=0,
    ))
    # ... 后续 agent/room/dept 导入同理
```

### 5.3 Agent / Room / Dept 导入

Agent 和 Room 的导入同样需要：
- 用 `i18n.display_name[当前语言]` 作为 DB `name`
- 将 `i18n` dict 直接写入 DB `i18n` 字段
- Dept 的 `dept_name` 和 `responsibility` 同理，优先从 `i18n` 读取

### 5.4 辅助函数

```python
def _resolve_i18n_text(i18n_text: I18nText | None, lang: str) -> str | None:
    """从 I18nText 中解析指定语言的文本。"""
    if not i18n_text or not isinstance(i18n_text, dict):
        return None
    return i18n_text.get(lang) or i18n_text.get(DEFAULT_LANG)
```

---

## 6. 后端 API 改造

### 6.1 语言读取

语言配置合并到已有的 `SystemStatusHandler` 返回中：

**GET /system/status.json**（已有接口，追加 `language` 字段）：

```json
{
  "initialized": true,
  "default_llm_server": 1,
  "schedule_state": "running",
  "language": "zh-CN"
}
```

### 6.2 语言切换

语言切换合并到 `settingController` 中，复用 setting 相关的 CRUD 模式：

**POST /config/language.json**

```json
// Request
{"language": "en"}

// Response
{"language": "en"}
```

> 读取走 `SystemStatusHandler`（前端启动时必调），切换走单独 POST 接口（写操作频率低，不必合并到 status）。

实现位于 `settingController.py`，调用 `configUtil.set_language()`。

### 6.3 现有 API 追加 display_name

以下列表 API 的返回数据追加 `display_name` 字段：

| API | 追加字段 |
|-----|---------|
| `GET /teams/list.json` | `team.display_name` |
| `GET /teams/<id>/detail.json` | `team.display_name`, `agents[].display_name`, `rooms[].display_name` |
| `GET /rooms/<id>/messages.json` | `sender_display_name`（可选，根据 agent i18n） |

`display_name` 由后端调用 `resolve_display_name()` 生成，前端直接使用。

### 6.4 路由注册

```python
# src/route.py 新增
(r"/config/language.json", settingController.LanguageHandler),
```

---

## 7. Web 前端改造

### 7.1 引入 vue-i18n

```bash
cd frontend && npm install vue-i18n@next
```

### 7.2 翻译资源文件

```text
frontend/src/locales/
├── zh-CN.json
└── en.json
```

**资源文件结构（示例）：**

```json
// zh-CN.json
{
  "topbar": {
    "settings": "系统设置",
    "selectTeam": "选择团队",
    "scheduleBlocked": "调度阻塞",
    "scheduleStopped": "调度停止",
    "switchToDark": "切换到暗色模式",
    "switchToLight": "切换到亮色模式"
  },
  "agent": {
    "status": {
      "active": "忙碌",
      "idle": "空闲",
      "failed": "失败"
    },
    "stop": "停止",
    "stopping": "停止中…",
    "resume": "恢复"
  },
  "dialog": {
    "confirm": "确认",
    "cancel": "取消"
  },
  "language": {
    "zhCN": "中文",
    "en": "English"
  }
}
```

```json
// en.json
{
  "topbar": {
    "settings": "Settings",
    "selectTeam": "Select Team",
    "scheduleBlocked": "Schedule Blocked",
    "scheduleStopped": "Schedule Stopped",
    "switchToDark": "Switch to Dark Mode",
    "switchToLight": "Switch to Light Mode"
  },
  "agent": {
    "status": {
      "active": "Active",
      "idle": "Idle",
      "failed": "Failed"
    },
    "stop": "Stop",
    "stopping": "Stopping…",
    "resume": "Resume"
  },
  "dialog": {
    "confirm": "Confirm",
    "cancel": "Cancel"
  },
  "language": {
    "zhCN": "中文",
    "en": "English"
  }
}
```

### 7.3 i18n 初始化

```typescript
// frontend/src/i18n.ts
import { createI18n } from 'vue-i18n';
import zhCN from './locales/zh-CN.json';
import en from './locales/en.json';

const i18n = createI18n({
  legacy: false,            // 使用 Composition API 模式
  locale: 'zh-CN',          // 默认语言，启动后从后端读取覆盖
  fallbackLocale: 'zh-CN',
  messages: { 'zh-CN': zhCN, en },
});

export default i18n;

export async function syncLanguageFromBackend(): Promise<void> {
  const resp = await fetch('/system/status.json');
  const { language } = await resp.json();
  i18n.global.locale.value = language;
}
```

### 7.4 组件改造模式

**改造前：**

```vue
<button>停止</button>
<span>忙碌</span>
```

**改造后：**

```vue
<button>{{ $t('agent.stop') }}</button>
<span>{{ $t('agent.status.active') }}</span>
```

**Script 中使用：**

```typescript
import { useI18n } from 'vue-i18n';
const { t } = useI18n();
showToast(t('agent.stopSuccess'));
```

### 7.5 语言切换 UI

在 `TopBar.vue` 设置区域添加语言切换下拉：

```vue
<select v-model="currentLocale" @change="onLanguageChange">
  <option value="zh-CN">中文</option>
  <option value="en">English</option>
</select>
```

切换时调用后端 API 持久化，并更新 vue-i18n locale：

```typescript
async function onLanguageChange(lang: string) {
  await setLanguage(lang);        // POST /config/language.json
  i18n.global.locale.value = lang;
}
```

### 7.6 需改造的组件清单

| 组件 | 硬编码文案数量（约） | 说明 |
|------|---------------------|------|
| `TopBar.vue` | 15+ | 团队选择、状态、设置、主题切换 |
| `AgentActivityDialog.vue` | 10+ | 状态标签、停止/恢复按钮、错误提示 |
| `ConfirmDialog.vue` | 2 | 确认/取消按钮默认文案 |
| `MessageStream.vue` | 3+ | 处理中提示、无消息占位 |
| `SettingsPage.vue` | 50+ | 设置面板各分区标题、表单标签、操作按钮 |
| `SettingsNavSidebar.vue` | 5+ | 导航菜单项 |
| `ModelsSettingsSection.vue` | 20+ | LLM 服务配置表单 |
| `RolesSettingsSection.vue` | 15+ | 角色模板编辑面板 |
| `GeneralSettingsSection.vue` | 10+ | 通用设置面板 |
| `RuntimeSettingsSection.vue` | 10+ | 运行时设置面板 |
| `TeamTreeEditor.vue` | 15+ | 团队树编辑器 |
| `ConsolePage.vue` | 5+ | 控制台页面 |
| `CustomSelect.vue` | 2+ | 自定义选择器 |

### 7.7 Preset 展示名处理

前端显示 Team/Agent/Room 名称时，优先使用后端 API 返回的 `display_name`，回退到 `name`：

```typescript
function getDisplayName(entity: { name: string; display_name?: string }): string {
  return entity.display_name || entity.name;
}
```

`display_name` 由后端根据当前语言解析后返回，前端不需要处理多语言映射逻辑。

---

## 8. TUI 改造

### 8.1 翻译层设计

TUI 使用 Python 字典作为轻量翻译层：

```python
# tui/i18n.py
_TRANSLATIONS: dict[str, dict[str, str]] = {
    "zh-CN": {
        "panel.chatRoom": "聊天室",
        "panel.teamMembers": "团队成员",
        "status.active": "忙碌",
        "status.idle": "空闲",
        "status.failed": "失败",
        "input.placeholder": "在此输入消息...",
        "input.observeMode": "当前为观察模式",
        "key.quit": "退出",
        "key.prevRoom": "上一个房间",
        "key.nextRoom": "下一个房间",
        "key.selectRoom": "切换到当前房间",
        "key.inputMode": "进入输入模式",
        "agent.processing": "处理中…",
        # ...
    },
    "en": {
        "panel.chatRoom": "Chat Room",
        "panel.teamMembers": "Team Members",
        "status.active": "Active",
        "status.idle": "Idle",
        "status.failed": "Failed",
        "input.placeholder": "Type a message...",
        "input.observeMode": "Observe mode",
        "key.quit": "Quit",
        "key.prevRoom": "Previous Room",
        "key.nextRoom": "Next Room",
        "key.selectRoom": "Select Room",
        "key.inputMode": "Enter Input Mode",
        "agent.processing": "Processing…",
        # ...
    },
}

_current_lang: str = "zh-CN"

def set_language(lang: str) -> None:
    global _current_lang
    _current_lang = lang

def t(key: str) -> str:
    """翻译函数。当前语言缺失时回退到 zh-CN。"""
    return (
        _TRANSLATIONS.get(_current_lang, {}).get(key)
        or _TRANSLATIONS["zh-CN"].get(key)
        or key
    )
```

### 8.2 TUI 组件改造

```python
# 改造前
yield Label("团队成员", classes="panel-title")
text = f"⟳ {agent_name} 处理中…"

# 改造后
from tui.i18n import t
yield Label(t("panel.teamMembers"), classes="panel-title")
text = f"⟳ {agent_name} {t('agent.processing')}"
```

### 8.3 语言初始化

TUI 启动时从后端 API 读取当前语言：

```python
# tui/app.py 启动流程
resp = await api_client.get("/config/language.json")
tui_i18n.set_language(resp["language"])
```

---

## 9. macOS 托盘菜单改造

### 9.1 翻译层

macOS 托盘复用与 TUI 相同的翻译字典模式：

```python
# src/app_i18n.py（或直接内嵌在 app_entry.py）
_TRAY_TRANSLATIONS = {
    "zh-CN": {
        "status": "状态",
        "openWeb": "打开 Web 界面",
        "openConfig": "打开配置目录",
        "resetData": "重置数据",
        "version": "版本",
        "quit": "退出",
        "starting": "启动中…",
        "running": "运行中",
        "stopped": "已停止",
        "startFailed": "启动失败",
        "resetConfirm": "确定要重置所有数据吗？\n所有聊天室、成员、消息记录将被删除，此操作不可撤销。",
        "resetSuccess": "重置成功",
        "resetSuccessMsg": "数据已清除，请重新启动程序。",
    },
    "en": {
        "status": "Status",
        "openWeb": "Open Web Console",
        "openConfig": "Open Config Folder",
        "resetData": "Reset Data",
        "version": "Version",
        "quit": "Quit",
        "starting": "Starting…",
        "running": "Running",
        "stopped": "Stopped",
        "startFailed": "Start Failed",
        "resetConfirm": "Are you sure you want to reset all data?\nAll rooms, agents, and messages will be deleted. This cannot be undone.",
        "resetSuccess": "Reset Complete",
        "resetSuccessMsg": "Data has been cleared. Please restart the application.",
    },
}
```

### 9.2 菜单构建

菜单项文案通过翻译函数获取；新增语言切换项：

```python
def _build_icon() -> pystray.Icon:
    lang = _get_current_language()  # 从 setting.json 读取
    tr = _TRAY_TRANSLATIONS.get(lang, _TRAY_TRANSLATIONS["zh-CN"])

    return pystray.Icon(
        name="TogoAgent",
        icon=_make_icon(),
        title="TogoAgent",
        menu=pystray.Menu(
            pystray.MenuItem(lambda _: f"{tr['status']}: {_backend_status}", None, enabled=False),
            pystray.MenuItem(tr["openWeb"], _on_open),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(tr["openConfig"], _on_open_config_dir),
            pystray.MenuItem(tr["resetData"], _on_reset_data),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🌐 中文 / English", pystray.Menu(
                pystray.MenuItem("中文", _on_set_language_zh, checked=lambda _: lang == "zh-CN"),
                pystray.MenuItem("English", _on_set_language_en, checked=lambda _: lang == "en"),
            )),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"{tr['version']}: v{__version__}", None, enabled=False),
            pystray.MenuItem(tr["quit"], _on_quit),
        ),
    )
```

### 9.3 语言切换回调

```python
def _on_set_language_zh(icon, item):
    _set_language_and_rebuild("zh-CN", icon)

def _on_set_language_en(icon, item):
    _set_language_and_rebuild("en", icon)

def _set_language_and_rebuild(lang: str, icon: pystray.Icon):
    configUtil.set_language(lang)
    icon.menu = _build_menu(lang)
    icon.update_menu()
```

---

## 10. 后端改动范围

| 文件 | 改动 |
|------|------|
| `src/util/configTypes.py` | `SettingConfig` 新增 `language`；`RoleTemplateConfig` / `AgentConfig` / `TeamRoomConfig` / `DeptNodeConfig` / `TeamConfig` 新增 `i18n: I18nData` 字段 |
| `src/util/configUtil.py` | 新增 `get_language()` / `set_language()`；`_save_setting_to_file()` 写回 language |
| `src/util/i18nUtil.py` | **新建**，`resolve_display_name()` 等工具函数 |
| `src/constants.py` | 无需改动（`SystemConfigKey` 不涉及，语言存 setting.json） |
| `src/model/dbModel/gtTeam.py` | 新增 `uuid` + `i18n` 字段 |
| `src/model/dbModel/gtAgent.py` | 新增 `i18n` 字段 |
| `src/model/dbModel/gtRoom.py` | 新增 `i18n` 字段 |
| `src/model/dbModel/gtRoleTemplate.py` | 新增 `i18n` 字段 |
| `src/service/presetService.py` | 适配新 Preset 格式；UUID 去重；写入 i18n 字段 |
| `src/service/roleTemplateService.py` | `save_role_template()` 保存 i18n |
| `src/controller/settingController.py` | 新增 `LanguageHandler`（POST 切换语言） |
| `src/controller/systemController.py` | `SystemStatusHandler` 返回追加 `language` 字段 |
| `src/controller/teamController.py` | 列表/详情 API 追加 `display_name` |
| `src/route.py` | 注册 `/config/language.json` |
| `src/app_entry.py` | 菜单项国际化 + 语言切换菜单 |
| `assets/preset/role_templates/*.json` | 改为内嵌多语言格式 |
| `assets/preset/teams/default.json` | 改为内嵌多语言格式 + UUID |
| `assets/config_template.json` | 新增 `language` 默认值 |

---

## 11. 前端改动范围

| 文件 | 改动 |
|------|------|
| `package.json` | 新增 `vue-i18n` 依赖 |
| `src/i18n.ts` | **新建**，i18n 初始化 + `syncLanguageFromBackend()` |
| `src/main.ts` | 注册 i18n 插件 |
| `src/locales/zh-CN.json` | **新建**，中文翻译资源 |
| `src/locales/en.json` | **新建**，英文翻译资源 |
| `src/api.ts` | 新增 `getLanguage()` / `setLanguage()` API 函数 |
| `src/components/TopBar.vue` | 文案替换 + 语言切换 UI |
| `src/components/AgentActivityDialog.vue` | 文案替换 |
| `src/components/ConfirmDialog.vue` | 默认按钮文案替换 |
| `src/components/MessageStream.vue` | 处理中提示替换 |
| `src/components/ChatPanel.vue` | 文案替换 |
| `src/pages/SettingsPage.vue` | 全量文案替换（最大工作量） |
| `src/components/settings/*.vue` | 各设置子面板文案替换 |
| `src/components/TeamTreeEditor.vue` | 文案替换 |
| `src/components/ConsoleChatPanel.vue` | 文案替换 |
| `src/pages/ConsolePage.vue` | 文案替换 |
| `src/components/CustomSelect.vue` | 文案替换 |

---

## 12. TUI 改动范围

| 文件 | 改动 |
|------|------|
| `tui/i18n.py` | **新建**，翻译字典 + `t()` 函数 |
| `tui/app.py` | 启动时从后端读取语言；绑定键描述替换 |
| `tui/widgets.py` | 面板标题、状态标签、提示文字替换 |
| `tui/api_client.py` | 新增 `get_language()` 调用（可选） |

---

## 13. 事件链路

### 13.1 Web Console 语言切换

```text
用户点击语言切换
  → TopBar 调用 setLanguage("en")
  → POST /config/language.json {"language": "en"}
  → settingController 调用 configUtil.set_language("en")
  → configUtil.update_setting() 原子写回 setting.json
  → 返回 {"language": "en"}
  → 前端更新 i18n.global.locale.value = "en"
  → vue-i18n 自动重新渲染所有 $t() 引用
  → 界面即时切换为英文
```

### 13.2 macOS 托盘语言切换

```text
用户点击 "English"
  → _on_set_language_en()
  → configUtil.set_language("en") 写回 setting.json
  → 重新构建菜单（使用英文翻译字典）
  → icon.update_menu() 刷新托盘菜单
```

### 13.3 TUI 启动读取语言

```text
TUI 启动
  → api_client.get("/system/status.json")
  → 获取 {"language": "en", ...}
  → tui_i18n.set_language("en")
  → 使用英文翻译字典渲染界面
```

### 13.4 Preset 导入流程

```text
系统启动
  → presetService.import_from_app_config()
  → 读取 assets/preset/role_templates/*.json（含 i18n.display_name）
  → 导入到 GtRoleTemplate（name + i18n 直接映射）
  → 读取 assets/preset/teams/default.json（含 uuid + i18n）
  → 按 UUID 去重，未导入过则创建
  → 用 i18n.display_name[当前语言] 作为 DB name
  → i18n dict 直接写入 GtTeam / GtAgent / GtRoom
```

---

## 14. 测试要点

### 14.1 后端单元测试

- `configUtil.set_language()` / `get_language()` 读写正确性
- `resolve_display_name()` 三层回退逻辑
- `presetService` UUID 去重：相同 UUID 不重复导入
- `presetService` name 回退去重：无 UUID 时按 name 去重
- `presetService` i18n 写入：导入后 GtTeam/GtAgent/GtRoom 的 i18n 字段正确
- `configTypes` 兼容性：旧格式 JSON（无 display_name）可正常解析
- `configTypes` 兼容性：I18nText 字段校验通过
- `LanguageHandler` API：GET 返回当前语言，POST 切换后 GET 验证

### 14.2 前端测试

- vue-i18n 初始化后默认 locale 正确
- `syncLanguageFromBackend()` 从 API 读取并设置 locale
- 切换 locale 后所有组件文案更新
- 翻译资源文件 key 一致性（zh-CN 和 en 的 key 集合相同）
- `getDisplayName()` 辅助函数回退逻辑

### 14.3 集成测试

- 端到端语言切换：Web 切换 → 后端持久化 → TUI 重启后读取正确
- Preset 导入后 API 返回的 display_name 根据当前语言变化
- 数据库迁移：已有数据加载后 i18n 为空 dict，display_name 回退到 name

---

## 15. 结论

V18 i18n 实现涉及 4 个层面：

1. **配置层**：`SettingConfig.language` 存储在 `setting.json`，提供 `get/set` API，多端共享
2. **数据层**：DB 模型新增 `i18n` JsonField，Preset 文件改为单文件内嵌多语言格式，导入时写入 i18n 字段
3. **展示层**：Web 前端引入 `vue-i18n`；TUI 和 macOS 托盘使用 Python 翻译字典；后端列表 API 追加 `display_name`
4. **交互层**：Web 顶栏 + macOS 托盘提供语言切换入口，切换即时生效

核心设计决策：
- **语言存后端**而非前端本地存储，确保多端同步
- **Preset 单文件内嵌**而非按语言分目录，避免 soul 等不翻译字段的重复
- **Preset 的 `i18n` 字段结构与 DB 模型一致**，导入时直接映射，无需转换
- **DB i18n 字段**而非运行时查翻译，切换语言时直接读 DB 无需重新导入
- **UUID 去重**而非 name 去重，防止语言切换导致重复导入
