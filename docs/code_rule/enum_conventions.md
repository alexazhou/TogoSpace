# 枚举使用规范

本文定义项目内 `EnhanceEnum` 及其子类的统一使用规则，目标是：

- 减少“枚举值 vs 枚举名”引发的兼容问题
- 明确哪些枚举可以 `auto()`，哪些必须保留字符串 `value`
- 保证 API / 配置 /协议字段稳定


## 1. 基本原则

项目内枚举统一继承 `EnhanceEnum`（见 `src/constants.py`）。

默认规则：

1. 内部语义枚举（仅用于代码分支，不对外暴露稳定文本）优先使用 `auto()`
2. 对外字段枚举（协议、配置、API、持久化 key）必须使用显式字符串 `value`
3. 对外字符串若有大小写或格式约定（如 `openai-compatible`、`claude_sdk`），禁止改为 `auto()`


## 2. `EnhanceEnum` 的匹配能力

`EnhanceEnum` 通过 `value_of` 与 `_missing_` 支持字符串解析，具备以下能力：

1. 大小写不敏感
2. 同时支持按 `name` 和按 `value` 匹配
3. 兼容 `-` / `_` / 空白差异（如 `openai-compatible`、`OPENAI_COMPATIBLE`、` openai_compatible `）

这意味着请求参数可写成多种大小写形式，但**不改变我们对外输出值的稳定性要求**。


## 3. 何时用 `auto()`

适用条件（同时满足）：

1. 该枚举不作为外部协议字段直接序列化
2. 不依赖 `value` 的具体文本
3. 不会写入需要长期稳定字符串的存储字段

示例：`RoomType`、`RoomState`、`AgentStatus`、`AgentHistoryTag`、`AgentHistoryStatus`、`AgentTaskType`、`AgentTaskStatus`、`TurnStepResult`、`ScheduleState` 等内部状态枚举可以使用 `auto()`。

> **注意**：`EnumField` / `EnumListField` 持久化时存储的是枚举 **name**（如 `"CANCELLED"`），而非 `value`。因此即便枚举值从字符串改为 `auto()`，已有数据库记录仍可正常读取。


## 4. 何时必须保留字符串 `value`

出现以下任一条件时，必须使用显式字符串值，不可 `auto()`：

1. 作为外部 API 字段直接返回或接收
2. 作为第三方协议字段（如 OpenAI role）
3. 作为配置文件中的固定字面量
4. 作为数据库或系统配置中的稳定 key

当前强制保留字符串 `value` 的枚举：

1. `OpenaiLLMApiRole`：OpenAI 协议字段（`system/user/assistant/tool`）
2. `LlmServiceType`：配置字段（如 `openai-compatible`）
3. `DriverType`：对外 API/配置字段（`native/claude_sdk/tsp`）
4. `RoleTemplateType`：对外字段（`system/user`，并要求保存小写字符串）
5. `SystemConfigKey`：数据库配置键（如 `working_directory`）


## 5. 代码注释要求

凡是“必须保留字符串 `value`”的枚举类，类定义上方必须注明：

1. 这是对外/持久化约定字段
2. 保存固定字符串（通常是小写）
3. 不使用 `auto()`

示例（`RoleTemplateType`）：

```python
class RoleTemplateType(EnhanceEnum):
    # 角色模板类型是对外字段约定，保存小写字符串，不使用 auto()。
    # 保留 "system" / "user" 两个固定值。
    SYSTEM = "system"
    USER = "user"
```


## 6. 序列化约定

1. DB `EnumField` 当前按 `name` 存储（见 `src/model/dbModel/base.py`）
2. 对外 API 返回时，如果是对外字符串约定字段，应返回其约定字符串（通常是 `.value`）
3. 若枚举为内部 `auto()` 类型，不应把 `.value` 当作稳定协议字段使用


## 7. 新增/修改枚举的检查清单

新增或改造枚举时，提交前至少检查：

1. 这个枚举是否会对外返回、对外接收、写配置、写 DB key
2. 若是对外字段：是否使用显式字符串 `value`，且注释写明“不使用 auto()”
3. 若改动了 `value`：是否会影响前后端协议、配置样例、历史数据
4. 是否补充或更新 `tests/unit/test_enhance_enum.py`
5. 涉及 API 行为时，是否补充对应 API/integration 测试


## 8. 测试要求

`tests/unit/test_enhance_enum.py` 应至少覆盖：

1. `name`/`value` 双路径匹配
2. 大小写不敏感
3. 连字符/下划线/空白兼容
4. Pydantic 模型中的解析行为
5. 无效值报错行为

