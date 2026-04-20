# Web 实体 i18n 下放前端方案

> 状态：设计中
> 适用范围：Web 前端消费的实体数据
> 非目标：历史消息发送者展示名快照

## 1. 背景

当前仓库已经具备多语言数据的存储基础：

- `GtTeam` / `GtAgent` / `GtRoom` / `GtRoleTemplate` 已有 `i18n` 字段
- Preset 导入流程已将原始多语言数据写入数据库
- Web 前端已经接入 `vue-i18n`

但现阶段 Web API 仍然广泛由后端根据 `setting.language` 预先解析展示字段，再将结果返回给前端，例如：

- `display_name`
- `initial_topic`

这会带来几个问题：

1. 后端承担了展示层的语言选择责任，与 Web 前端耦合过深。
2. 前端切换语言时，实体展示依赖重新请求接口，而不是本地重渲染。
3. Controller 中存在大量重复的 `extract_i18n_str(...)` 逻辑。
4. 接口同时承载“稳定业务字段”和“按语言派生的展示字段”，职责边界不清晰。

本方案的目标是将 Web 实体的多语言展示责任明确下放给前端：后端返回稳定业务字段和原始 `i18n` 数据，前端基于当前 locale 自行解析展示文案。

## 2. 目标与非目标

### 2.1 目标

1. Web API 对实体统一返回原始 `i18n` 数据，而不是预先计算好的展示值。
2. 前端统一负责 `display_name`、`initial_topic` 等实体字段的本地化解析与 fallback。
3. 语言切换后，前端实体展示可直接本地重渲染，不依赖重新请求接口。
4. 减少 Controller 中与 Web 展示相关的语言分支逻辑。

### 2.2 非目标

1. 不处理历史消息中发送者名称的多语言快照。
2. 不改变后端自产生的系统文案翻译方式。
3. 不在本次方案中重构 TUI / tray 的 i18n 机制。

## 3. 设计原则

### 3.1 稳定标识与展示字段分离

- `name` 是稳定业务标识，不参与翻译。
- 展示相关字段从 `i18n` 中解析。

### 3.2 后端只传原始数据，不做 Web 展示决策

对 Web 实体接口而言，后端职责是：

- 返回稳定业务字段
- 返回原始 `i18n`

后端不负责：

- 按当前语言为 Web 前端挑选 `display_name`
- 为 Web 前端提前计算 `initial_topic`

### 3.3 前端统一解析，避免组件各自实现

前端通过统一 helper 解析 `i18n`，避免每个组件分别处理 locale、fallback、空值裁剪等细节。

### 3.4 后端自产生文本仍允许在后端翻译

以下内容不属于“Web 实体展示字段”，可继续由后端根据语言生成：

- TUI 固定文案
- tray 菜单文案
- 后端系统消息
- 后端运行提示语

## 4. 数据模型约定

### 4.1 统一的 i18n 结构

实体中的 `i18n` 使用统一结构：

```json
{
  "display_name": {
    "zh-CN": "默认团队",
    "en": "Default Team"
  },
  "initial_topic": {
    "zh-CN": "这里是默认房间",
    "en": "This is the default room"
  }
}
```

即：

- 第一层 key：业务字段名
- 第二层 key：语言代码
- value：对应语言文本

### 4.2 字段归属规则

| 字段 | 归属 | 说明 |
|------|------|------|
| `name` | 稳定业务字段 | 不翻译，不随语言切换变化 |
| `i18n.display_name` | 展示字段 | 用于前端展示名称 |
| `initial_topic` | 业务兜底字段 | 兼容旧数据或无 i18n 的用户自建房间 |
| `i18n.initial_topic` | 展示字段 | 用于前端按语言展示房间介绍 |
| `i18n.dept_name` | 展示字段 | 部门显示名 |
| `i18n.responsibility` | 展示字段 | 部门职责文案 |

## 5. 接口契约

### 5.1 Team / Agent / Room / RoleTemplate

Web 前端消费的实体接口建议统一返回：

```json
{
  "id": 1,
  "name": "default_team",
  "i18n": {
    "display_name": {
      "zh-CN": "默认团队",
      "en": "Default Team"
    }
  }
}
```

其中：

- `name` 用于稳定引用、跳转、联动、调试
- `i18n` 用于展示

### 5.2 Room 示例

```json
{
  "id": 10,
  "team_id": 1,
  "name": "general",
  "type": "GROUP",
  "initial_topic": "这里是默认房间",
  "max_turns": 100,
  "agent_ids": [1, 2],
  "biz_id": null,
  "tags": [],
  "i18n": {
    "display_name": {
      "zh-CN": "大厅",
      "en": "General"
    },
    "initial_topic": {
      "zh-CN": "这里是默认房间",
      "en": "This is the default room"
    }
  }
}
```

说明：

- `initial_topic` 继续保留，作为旧数据与无 i18n 数据的业务兜底字段。
- 前端展示时优先读 `i18n.initial_topic`。

### 5.3 WebSocket / runtime 快照

运行态数据也应遵循相同原则：返回稳定字段和原始 `i18n`。

示例：

```json
{
  "event": "room_status",
  "gt_room": {
    "id": 10,
    "team_id": 1,
    "name": "general",
    "i18n": {
      "display_name": {
        "zh-CN": "大厅",
        "en": "General"
      }
    }
  },
  "current_turn_agent": {
    "id": 2,
    "name": "alice",
    "i18n": {
      "display_name": {
        "zh-CN": "小王",
        "en": "Alice"
      }
    }
  }
}
```

### 5.4 兼容期策略

迁移过程中允许接口短期同时返回：

- `i18n`
- 旧字段 `display_name`

但新代码只应消费 `i18n`，`display_name` 仅用于兼容过渡。过渡结束后删除旧字段。

## 6. 前端展示规则

### 6.1 统一 fallback

前端解析展示字段时统一使用以下优先级：

1. `i18n[field][currentLocale]`
2. `i18n[field]["zh-CN"]`
3. 业务兜底字段，例如 `initial_topic`
4. `name`

### 6.2 前端 helper 建议

建议在 `frontend/src/utils.ts` 或单独的 `frontend/src/i18nEntity.ts` 中提供统一方法：

```ts
export type I18nText = Record<string, string>;
export type EntityI18n = Record<string, I18nText>;

export function resolveI18nField(
  i18n: EntityI18n | undefined,
  field: string,
  fallback?: string | null,
  locale = i18nGlobal.locale.value,
): string {
  return i18n?.[field]?.[locale]?.trim()
    || i18n?.[field]?.['zh-CN']?.trim()
    || fallback?.trim()
    || '';
}

export function resolveDisplayName(
  entity: { name: string; i18n?: EntityI18n | null },
): string {
  return resolveI18nField(entity.i18n ?? undefined, 'display_name', entity.name);
}
```

### 6.3 Special Agent 规则

`SYSTEM` / `OPERATOR` 不建议依赖后端返回 `display_name`。

Web 前端可直接按固定 key 映射：

- `specialAgent.system`
- `specialAgent.operator`

原因：

- 特殊成员不是用户配置实体
- 语义固定
- 前端自行翻译更简单、职责更清晰

## 7. 后端改造清单

### 7.1 应停止为 Web API 做语言解析的路径

以下位置目前存在 `extract_i18n_str(...)` 参与 Web 响应拼装，后续应逐步移除：

- `src/controller/teamController.py`
- `src/controller/agentController.py`
- `src/controller/roomController.py`
- `src/service/roomService.py`

改造方向：

1. 返回实体原始字段与 `i18n`
2. 不再依赖 `configUtil.get_language()` 选择 Web 展示字段
3. 尽量复用 ORM / model 的自动序列化能力，减少手拼 JSON

### 7.2 可保留后端翻译的路径

以下路径可继续保留后端翻译逻辑：

- `src/util/i18nUtil.py` 中的通用文案翻译
- `src/service/roomService.py` 中系统欢迎语等后端自产生文案
- TUI / tray 相关显示文案

## 8. 前端改造清单

### 8.1 类型层

需要为以下实体补充统一的 `i18n` 类型：

- `AgentInfo`
- `AgentSnapshot`
- `TeamSummary`
- `TeamDetail`
- `TeamMember`
- `TeamRoomDetail`
- `RoleTemplateSummary`
- `RoleTemplateDetail`

文件：

- `frontend/src/types.ts`

### 8.2 API 归一化层

`frontend/src/api.ts` 目前会将后端返回结果归一为前端类型，需要补充：

1. 透传 `i18n`
2. 不依赖 `display_name`
3. Room / Agent / Team / RoleTemplate 统一规范

### 8.3 实时事件归一化层

`frontend/src/realtime/eventNormalizer.ts` 需要支持：

1. `gt_room.i18n`
2. `current_turn_agent.i18n`
3. 其他运行态实体快照中的 `i18n`

### 8.4 组件替换点

组件中的 `displayName(name, display_name)` 应逐步替换为统一的 `resolveDisplayName(entity)`。

高优先级路径包括：

- 团队切换器
- Team 详情页
- Room 列表 / Chat 面板
- Agent 列表
- 设置页 Team / RoleTemplate 相关面板

## 9. 迁移步骤

### 阶段 1：后端补齐 `i18n`

目标：

- 所有相关 Web API 和 WebSocket 快照补齐 `i18n`
- 兼容期保留 `display_name`

收益：

- 前端可以先切换消费逻辑，无需一次性修改后端全部字段

### 阶段 2：前端切换到 `i18n`

目标：

- 类型、API 归一化、实时事件归一化、组件展示逻辑统一改为消费 `i18n`
- 语言切换后依赖本地重渲染

### 阶段 3：清理后端展示派生字段

目标：

- 删除 Web API 中多余的 `display_name`
- 移除 Controller / runtime 中专门为 Web 展示做的 `extract_i18n_str(...)`

## 10. 测试建议

### 10.1 后端

重点验证：

1. Team / Agent / Room / RoleTemplate 相关接口已返回 `i18n`
2. 用户自建内容无 `i18n` 时，接口仍能返回稳定字段
3. 兼容期内旧字段仍存在
4. WebSocket `room_status` / `agent_status` / 运行态快照包含 `i18n`

### 10.2 前端

重点验证：

1. 中文模式下展示 `i18n` 中文值
2. 英文模式下展示 `i18n` 英文值
3. 缺少英文翻译时回退到 `zh-CN`
4. 无 `i18n` 的用户自建实体回退到 `name` 或业务兜底字段
5. 语言切换后无需重新请求列表即可更新实体展示

## 11. 已知取舍

### 11.1 为什么保留 `initial_topic`

`initial_topic` 既是展示内容，也是部分旧流程中的业务字段。当前方案中保留它作为后端稳定兜底字段，可降低对旧数据和旧逻辑的侵入。

### 11.2 为什么不在本次处理历史消息

历史消息展示名是否应当随实体当前语言切换、是否需要保留发送时快照，是独立设计问题。该问题与“当前实体由前端解析 i18n”不强耦合，本次方案先明确排除，避免范围失控。

## 12. 建议结论

建议将团队约定明确为：

1. Web API 返回稳定业务字段和原始 `i18n`。
2. Web 前端负责实体展示字段的 locale 解析与 fallback。
3. 后端不再为 Web 前端进行 `display_name` / `initial_topic` 的展示层转换。
4. 后端仅保留自身系统文案的翻译职责。

该方案能显著降低前后端耦合，提升语言切换体验，并与“后端尽量返回业务真实数据、展示由前端处理”的开发约定保持一致。
