# TogoSpace 国际化（i18n）设计方案

> 文档版本：v1.0
> 创建日期：2026-04-10
> 状态：待实施

## 1. 背景与目标

TogoSpace 项目需要添加国际化支持，主要目标：

1. **初期支持英文**：为国际用户提供英文界面
2. **保留扩展空间**：后续可支持日文、韩文等其他语言
3. **覆盖三端**：Python 后端、Textual TUI 前端、Vue Web 前端

## 2. 当前状态分析

### 2.1 后端 (Python/Tornado)

| 位置 | 问题 | 示例 |
|------|------|------|
| `src/controller/*.py` | 中英文混合错误消息 | `"Role template '{name}' already exists"`、`"角色模板不存在"` |
| `src/util/assertUtil.py` | 硬编码错误消息 | `assertNotNull(..., error_message="Team ID not found")` |
| `src/controller/baseController.py` | 默认错误文本 | `"Internal Server Error"` |

**改造数量估算**：约 50+ 处错误消息

### 2.2 TUI 前端 (Textual)

| 位置 | 文本类型 | 示例 |
|------|----------|------|
| `tui/widgets.py` | 面板标题 | "聊天室"、"团队成员" |
| `tui/widgets.py` | 状态消息 | "已连接"、"已断开"、"重连中" |
| `tui/widgets.py` | 提示文本 | "暂无消息"、"请选择一个房间" |
| `tui/widgets.py` | Agent 状态 | "忙碌"、"失败"、"空闲" |

**改造数量估算**：约 30+ 处 UI 文本

### 2.3 Web 前端 (Vue 3)

| 位置 | 文本类型 | 示例 |
|------|----------|------|
| `TopBar.vue` | 导航/团队 | "选择团队"、"启用中"、"已停用" |
| `ChatPanel.vue` | 聊天界面 | "暂无房间"、"调度中"、"成员列表"、"发送" |
| `SidebarPanel.vue` | 侧边栏 | 各类标题和提示 |
| `SettingsPage.vue` | 设置页 | 各类配置项标签 |

**改造数量估算**：约 20+ 个组件，每个组件 5-10 处文本

**现状**：`package.json` 未安装 vue-i18n

## 3. 设计方案

### 3.1 语言文件组织

#### 后端结构

```
src/i18n/
├── __init__.py        # 模块入口，导出便捷函数
├── core.py            # i18n 核心类
└── locales/
    ├── zh.json        # 中文（默认）
    ├── en.json        # 英文
    └── ja.json        # 日文（预留）
```

#### Web 前端结构

```
frontend/src/i18n/
├── index.ts           # vue-i18n 初始化
└── locales/
    ├── zh.ts          # 中文
    ├── en.ts          # 英文
    └── ja.ts          # 日文（预留）
```

### 3.2 后端 i18n 核心模块

#### API 设计 (`src/i18n/core.py`)

```python
class I18n:
    """国际化核心类"""

    def __init__(self, locale: str = "zh"):
        self._locale = locale
        self._messages: dict[str, dict[str, str]] = {}

    def load_locale(self, locale: str, path: str) -> None:
        """从 JSON 文件加载语言包"""

    def set_locale(self, locale: str) -> None:
        """切换当前语言"""

    def t(self, key: str, **kwargs) -> str:
        """翻译消息，支持 {placeholder} 占位符替换"""

    def get_locale(self) -> str:
        """获取当前语言代码"""

# 全局单例
_i18n: I18n | None = None

def init_i18n(locale: str = "zh") -> I18n:
    """初始化全局 i18n 实例"""

def get_i18n() -> I18n:
    """获取全局实例"""

def t(key: str, **kwargs) -> str:
    """便捷翻译函数：t("error.team_not_found", id="123")"""
```

#### 语言文件格式 (`src/i18n/locales/en.json`)

```json
{
  "error": {
    "team_not_found": "Team ID '{id}' not found",
    "room_not_found": "Room ID '{id}' not found",
    "template_not_found": "Role template '{id}' not found",
    "template_exists": "Role template '{name}' already exists",
    "template_in_use": "Role template '{name}' is in use",
    "system_template_delete": "System template cannot be deleted",
    "agent_not_found": "Agent '{name}' not found in team '{team}'",
    "agent_not_in_team": "Agent IDs not in current team: {ids}",
    "duplicate_names": "Duplicate agent names: {names}",
    "room_exists": "Room '{name}' already exists",
    "room_min_agents": "Room must have at least 2 agents",
    "invalid_request": "Invalid request: {reason}",
    "internal_error": "Internal Server Error"
  },
  "status": {
    "connected": "Connected",
    "disconnected": "Disconnected",
    "reconnecting": "Reconnecting...",
    "idle": "Idle",
    "busy": "Busy",
    "failed": "Failed"
  },
  "ui": {
    "chat_room": "Chat Room",
    "team_members": "Team Members",
    "no_room": "No Room Selected",
    "no_message": "No messages",
    "select_room": "Please select a room",
    "room_not_exist": "Room does not exist",
    "no_members": "This room has no members",
    "messages_count": "Messages: {count}",
    "unread": "Unread"
  }
}
```

#### 配置扩展 (`src/util/configTypes.py`)

```python
class SettingConfig(BaseModel):
    # 现有字段...
    locale: str = "zh"  # 新增：默认语言，可选 "zh" | "en"
```

### 3.3 TUI 前端改造

TUI 与后端共享同一套 i18n 模块：

```python
# tui/widgets.py 改造示例
from i18n import t

class RoomPanel(Vertical):
    def compose(self) -> ComposeResult:
        yield Label(t("ui.chat_room"), classes="panel-title")
        yield ListView(id="room-list")
        yield Label(t("ui.team_members"), classes="panel-title")
        yield ListView(id="member-list")

    def _get_agent_status_markup(self, status: str) -> str:
        if status.lower() == "active":
            return f"[bold #56d4b0]● {t('status.busy')}[/]"
        if status.lower() == "failed":
            return f"[bold #f85149]● {t('status.failed')}[/]"
        return f"[#7f91a4]○ {t('status.idle')}[/]"

class StatusBar(Static):
    def set_connected(self) -> None:
        self.status_markup = f"[bold #56d4b0]● {t('status.connected')}[/]"

    def set_reconnecting(self) -> None:
        self.status_markup = f"[bold #e3b341]◌ {t('status.reconnecting')}[/]"
```

### 3.4 Web 前端方案

#### 依赖安装

```bash
cd frontend && npm install vue-i18n
```

#### 初始化 (`frontend/src/i18n/index.ts`)

```typescript
import { createI18n } from 'vue-i18n';
import en from './locales/en';
import zh from './locales/zh';

// 从 localStorage 读取用户偏好，默认中文
const savedLocale = localStorage.getItem('locale') || 'zh';

const i18n = createI18n({
  legacy: false,           // 使用 Composition API 模式
  locale: savedLocale,
  fallbackLocale: 'en',    // 缺失翻译回退到英文
  messages: { en, zh },
});

export default i18n;

// 便捷函数
export function t(key: string, params?: Record<string, unknown>): string {
  return i18n.global.t(key, params);
}

// 语言切换
export function setLocale(locale: string): void {
  i18n.global.locale.value = locale;
  localStorage.setItem('locale', locale);
}

export function getCurrentLocale(): string {
  return i18n.global.locale.value;
}
```

#### 语言文件 (`frontend/src/i18n/locales/en.ts`)

```typescript
export default {
  common: {
    close: 'Close',
    save: 'Save',
    cancel: 'Cancel',
    delete: 'Delete',
    edit: 'Edit',
    create: 'Create',
    confirm: 'Confirm',
  },
  connection: {
    connected: 'Connected',
    disconnected: 'Disconnected',
    reconnecting: 'Reconnecting',
    messages_count: '{count} messages',
  },
  team: {
    select_team: 'Select Team',
    enabled: 'Enabled',
    disabled: 'Disabled',
    teams_count: '{count} Teams',
    team_disabled: 'This team is disabled',
  },
  room: {
    no_room: 'No Room',
    scheduling: 'Scheduling',
    idle: 'Idle',
    member_list: 'Members {count}',
    room_members: 'Room Members',
    members_count: '{count} people',
    no_members: 'This room has no members.',
    current_speaker: 'Speaking: {name}',
  },
  chat: {
    loading_messages: 'Loading messages...',
    enter_message: 'Enter message here...',
    send: 'Send',
    enter_hint: 'Press Enter to send, Shift+Enter for new line',
  },
  settings: {
    title: 'Settings',
    general: 'General',
    models: 'Models',
    teams: 'Teams',
    roles: 'Roles',
    runtime: 'Runtime',
    breadcrumb_home: 'Home',
    breadcrumb_settings: 'Settings',
  },
  topbar: {
    brand: 'Team Agent Web Console',
    system_settings: 'System Settings',
    theme_light: 'Switch to Light Mode',
    theme_dark: 'Switch to Dark Mode',
  },
};
```

#### 组件改造示例

```vue
<script setup lang="ts">
import { t } from '../i18n';

const activeTeamName = computed(() => (
  props.teams.find((team) => team.id === props.activeTeamId)?.name ?? t('team.select_team')
));
</script>

<template>
  <header class="topbar">
    <p class="eyebrow">{{ t('topbar.brand') }}</p>
    <span class="team-switcher-button__label">{{ activeTeamName }}</span>
    <span class="team-switcher-group__title">{{ t('team.enabled') }}</span>
    <span class="team-switcher-group__count">{{ t('team.teams_count', { count: enabledTeams.length }) }}</span>
    <button :title="t('topbar.system_settings')" @click="emit('openSettings')">
      <!-- icon -->
    </button>
    <div class="metric-pill">{{ t('connection.messages_count', { count: totalMessageCount }) }}</div>
  </header>
</template>
```

### 3.5 语言切换机制

#### 后端（配置级别）

- 语言在 `setting.json` 中配置：`{"locale": "en"}`
- 启动时读取配置，初始化 i18n 实例
- **不支持请求级别切换**（简化实现，避免增加复杂度）

#### Web 前端（顶部栏切换）

- localStorage 存储用户语言偏好
- 在 TopBar 状态区域旁添加语言切换按钮
- 切换后实时更新所有组件（Vue 响应式）

```vue
<!-- TopBar.vue 中添加语言切换按钮 -->
<button
  class="locale-switch nav-action"
  type="button"
  @click="toggleLocale"
  :title="currentLocale === 'zh' ? 'Switch to English' : '切换到中文'"
>
  {{ currentLocale === 'zh' ? 'EN' : '中文' }}
</button>

<script setup lang="ts">
import { setLocale, getCurrentLocale } from '../i18n';

const currentLocale = ref(getCurrentLocale());

function toggleLocale() {
  const next = currentLocale.value === 'zh' ? 'en' : 'zh';
  setLocale(next);
  currentLocale.value = next;
}
</script>
```

## 4. 实施计划

### Phase 1: 基础框架搭建

| 任务 | 预计工时 |
|------|----------|
| 创建 `src/i18n/` 模块结构和核心类 | 1h |
| 创建 `src/i18n/locales/zh.json`（整理现有文本） | 2h |
| 创建 `src/i18n/locales/en.json`（英文翻译） | 2h |
| 扩展 `SettingConfig` 添加 locale 字段 | 0.5h |
| 安装 vue-i18n 并创建 `frontend/src/i18n/` | 0.5h |
| 创建 `frontend/src/i18n/locales/en.ts`、`zh.ts` | 1h |

### Phase 2: 后端改造

| 任务 | 预计工时 |
|------|----------|
| 改造 `src/util/assertUtil.py` 支持 i18n | 0.5h |
| 改造 `src/controller/*.py` 错误消息（50+ 处） | 2h |
| 改造 `src/controller/baseController.py` 默认消息 | 0.5h |

### Phase 3: TUI 改造

| 任务 | 预计工时 |
|------|----------|
| 改造 `tui/widgets.py` UI 文本（30+ 处） | 1h |
| 改造 `tui/app.py` 其他 UI 文本 | 0.5h |

### Phase 4: Web 前端改造

| 任务 | 预计工时 |
|------|----------|
| 在 `main.ts` 初始化 i18n | 0.5h |
| 改造 TopBar.vue（含语言切换按钮） | 1h |
| 改造 ChatPanel.vue | 1h |
| 改造其他组件（SidebarPanel、SettingsPage 等） | 2h |

**总工时估算**：约 12-15 小时

## 5. 文件变更清单

### 新增文件

| 路径 | 说明 |
|------|------|
| `src/i18n/__init__.py` | 模块入口 |
| `src/i18n/core.py` | i18n 核心类 |
| `src/i18n/locales/zh.json` | 中文语言包 |
| `src/i18n/locales/en.json` | 英文语言包 |
| `frontend/src/i18n/index.ts` | vue-i18n 初始化 |
| `frontend/src/i18n/locales/zh.ts` | 中文语言包 |
| `frontend/src/i18n/locales/en.ts` | 英文语言包 |

### 修改文件

| 路径 | 改动内容 |
|------|----------|
| `src/util/configTypes.py` | 添加 `locale` 字段 |
| `src/util/assertUtil.py` | 错误消息改用 `t()` |
| `src/controller/*.py` | 错误消息改用 `t()` |
| `src/controller/baseController.py` | 默认错误消息改用 `t()` |
| `tui/widgets.py` | UI 文本改用 `t()` |
| `tui/app.py` | UI 文本改用 `t()` |
| `frontend/package.json` | 添加 vue-i18n 依赖 |
| `frontend/src/main.ts` | 初始化 i18n 插件 |
| `frontend/src/components/*.vue` | UI 文本改用 `t()` |

## 6. 验证与测试

### 6.1 后端验证

1. 修改 `~/.togospace/setting.json`：`{"locale": "en"}`
2. 启动后端：`./scripts/start_backend.sh`
3. 调用 API 触发错误（如请求不存在的 team）
4. 验证 `error_desc` 返回英文消息

### 6.2 TUI 验证

1. 修改配置 locale 为 "en"
2. 启动 TUI：`./scripts/start_tui.sh`
3. 验证所有面板标题、状态消息为英文

### 6.3 Web 前端验证

1. 启动前端：`cd frontend && npm run dev`
2. 点击 TopBar 语言切换按钮
3. 验证所有组件文本实时切换
4. 刷新页面，验证语言偏好已持久化

### 6.4 回归测试

```bash
./scripts/run_tests.sh
```

确保改造未引入破坏性变更。

## 7. 扩展预留

### 新增语言步骤

1. **后端**：创建 `src/i18n/locales/ja.json`，填写翻译
2. **前端**：创建 `frontend/src/i18n/locales/ja.ts`，填写翻译
3. **前端**：在 `i18n/index.ts` 的 messages 中注册新语言
4. **前端**：TopBar 语言切换按钮支持新选项

### 设计考量

- 语言键采用层级结构（`error.xxx`、`ui.xxx`、`status.xxx`），便于分类维护
- 占位符使用 `{key}` 格式，与 Vue i18n 兼容
- 后端 i18n 支持动态加载语言文件（无需重启）
- Web 前端 vue-i18n 支持 fallbackLocale，缺失翻译自动回退到英文

## 8. 决策记录

| 决策项 | 选择 | 原因 |
|--------|------|------|
| 后端语言切换级别 | 配置级别 | 简化实现，避免请求级别复杂度 |
| 前端语言切换位置 | TopBar | 快速切换，无需进入设置页面 |
| 实施优先级 | 全部同步 | 三端同时改造，避免遗漏 |
| 后端语言文件格式 | JSON | 与前端一致，便于统一维护 |