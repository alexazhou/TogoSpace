# V12: 大模型服务配置与动态切换 - 产品文档

## 目标

将 LLM 服务配置从静态文件（`~/.team_agent/setting.json`）提升为 **Web Console 可视化管理**能力，使用户无需手动编辑 JSON 即可完成 LLM 服务的查看、新增、编辑、删除、切换默认服务等操作；同时支持在运行时动态切换当前活跃的 LLM 服务，Agent 在下一次推理时自动使用新配置，无需重启后端。

本文档默认描述的是**后端服务能力**；Web 前端作为配置的管理界面和消费方。

---

## 功能特性

### 一、LLM 服务列表管理

- **服务列表展示**：在 Web Console 的"大模型服务管理"页面，展示所有已配置的 LLM 服务，包括名称、模型、类型、启用状态、是否为默认服务。
- **新增服务**：通过表单新增一个 LLM 服务配置，包含必填字段（名称、Base URL、API Key、类型、模型）和可选字段（Extra Headers、Token 预算参数）。
- **编辑服务**：修改已有服务的配置（名称不可修改，作为唯一标识）。
- **删除服务**：移除不需要的服务配置。当前默认服务不可直接删除，需先切换默认后再删除。
- **启用 / 禁用**：单独开关控制某个服务是否参与候选，禁用后的服务不会出现在"可切换"列表中。

### 二、默认服务切换

- **运行时切换**：用户可在服务列表中选择一个已启用的服务设为"默认服务"（即 `default_llm_server`），切换立即生效。
- **无需重启**：切换默认服务后，后端内存中的 `SettingConfig` 动态更新；Agent 在下一次推理请求时，自动读取最新的 `current_llm_service`。
- **切换反馈**：前端在切换后展示成功提示，并刷新当前默认服务标记。

### 三、连通性测试

- **测试连接**：用户可对任意 LLM 服务发起连通性测试，后端向目标服务发送一个最小化的推理请求（如 `"hi"`），验证 Base URL、API Key、模型名称是否可用。
- **测试结果反馈**：测试成功时返回模型实际响应的基本信息（如响应耗时、使用的 token 数）；测试失败时返回具体错误原因（如认证失败、网络不通、模型不存在等）。
- **不影响运行**：测试请求独立于 Agent 推理流程，不占用 Agent 事件队列，不写入任何消息历史或活动记录。
- **支持未保存配置测试**：新增服务时，用户可在保存前先测试配置是否可用，避免保存无效配置。

### 四、配置持久化

- **内存热更新 + 异步写回**：通过 Web Console 做的修改，后端直接更新内存中的 `AppConfig.setting` 缓存，同时异步写回 `setting.json`，确保重启后配置不丢失。
- **配置校验**：后端在保存前校验必填字段、URL 格式、类型枚举等，不合法的配置拒绝保存。

---

## 用户价值

### 1. 告别手动编辑 JSON

用户不再需要 SSH 到服务器上编辑 `setting.json` 来添加或修改 LLM 服务，所有操作可在浏览器中完成。

### 2. 运行时热切换

在多模型场景下（如同时配置 qwen、deepseek、gpt），用户可随时切换默认模型，无需重启后端，Agent 立即使用新模型。

### 3. 降低配置出错率

表单化的输入带有字段校验和类型提示，相比手写 JSON 更不容易出错。

### 4. 为多模型协作打基础

服务列表管理 + 调用策略（预留）为后续 Agent 级别模型分配、场景化路由等高级能力奠定配置基础。

---

## 核心概念

### LLM 服务（LlmServiceConfig）

一个 LLM 服务配置对应一个模型 API 端点，包含以下核心字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 服务唯一标识，创建后不可修改 |
| `base_url` | string | API 端点地址 |
| `api_key` | string | 鉴权密钥（当前阶段明文存储，后续加密） |
| `type` | enum | 服务类型：`openai-compatible`、`anthropic`、`google`、`deepseek` |
| `model` | string | 使用的模型名称，如 `qwen-plus`、`gpt-4o` |
| `enable` | bool | 是否启用 |
| `extra_headers` | dict | 附加请求头 |
| `context_window_tokens` | int | 上下文窗口大小 |
| `reserve_output_tokens` | int | 预留输出 token |
| `compact_trigger_ratio` | float | 自动压缩触发比例 |
| `compact_summary_max_tokens` | int | 摘要最大 token |

### 默认服务（default_llm_server）

`SettingConfig.default_llm_server` 指向一个已启用服务的 `name`。当 Agent 发起推理时，`llmService` 通过 `current_llm_service` 属性获取当前活跃服务。

### 配置热更新

切换默认服务或修改服务配置后，后端通过修改内存中缓存的 `AppConfig.setting` 实现热更新。由于 `llmService` 每次推理时都从 `configUtil.get_app_config().setting.current_llm_service` 实时读取，无需额外通知机制。

---

## 效果演示

### 服务列表页

```text
┌─────────────────────────────────────────────────────┐
│ 系统设置 / 大模型服务管理                             │
│                                                     │
│ ┌─ 默认服务 ──────────────────────────────────────┐ │
│ │  ★ qwen (默认)                                  │ │
│ │     模型：qwen-plus                              │ │
│ │     类型：openai-compatible                      │ │
│ │     端点：https://dashscope.aliyuncs.com/...     │ │
│ │     状态：已启用                                  │ │
│ │     [编辑] [测试连接] [设为默认 ✓]                  │ │
│ ├─────────────────────────────────────────────────┤ │
│ │  deepseek                                       │ │
│ │     模型：deepseek-chat                          │ │
│ │     类型：deepseek                               │ │
│ │     状态：已启用                                  │ │
│ │     [编辑] [测试连接] [设为默认]                   │ │
│ ├─────────────────────────────────────────────────┤ │
│ │  gpt                                            │ │
│ │     模型：gpt-4o                                 │ │
│ │     类型：openai-compatible                      │ │
│ │     状态：已禁用                                  │ │
│ │     [编辑] [测试连接] [启用]                      │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ [+ 新增服务]                                         │
└─────────────────────────────────────────────────────┘
```

### 新增 / 编辑服务表单

```text
┌─ 编辑 LLM 服务 ───────────────────────┐
│ 名称：deepseek                        │
│ Base URL：https://api.deepseek.com/v1 │
│ API Key：sk-****（点击修改）           │
│ 类型：[deepseek ▼]                    │
│ 模型：deepseek-chat                   │
│ 启用：[✓]                             │
│                                       │
│ ── 高级配置（可折叠）──               │
│ 上下文窗口 tokens：131072             │
│ 预留输出 tokens：8192                 │
│ compact 触发比例：0.85                │
│ 摘要最大 tokens：2048                 │
│ Extra Headers：{"User-Agent":"..."}   │
│                                       │
│ [取消]  [保存]                         │
└───────────────────────────────────────┘
```

---

## 产品边界

### V12 包含

- LLM 服务列表的查看、新增、编辑、删除
- 服务启用 / 禁用开关
- 默认服务的运行时切换
- LLM 服务的连通性测试（支持已保存和未保存的配置）
- 配置变更写回 `setting.json` 持久化（内存热更新 + 异步写回）
- 后端配置字段校验
### V12 不包含

- 按场景分配模型的路由逻辑（聊天/工具/摘要分别指定模型）
- Agent 级别的模型覆盖 UI（Agent 已有 `model` 字段，此处不涉及）
- API Key 的掩码展示与加密存储（当前阶段全部明文，后续再加密）
- 多用户权限隔离（当前系统无用户鉴权体系）

---

## 后端 API 设计

### LLM 服务管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/config/llm_services/list.json` | 获取所有 LLM 服务列表 |
| `POST` | `/config/llm_services/create.json` | 新增 LLM 服务（请求体复用 LlmServiceConfig） |
| `POST` | `/config/llm_services/{index}/modify.json` | 修改指定服务配置（含启用/禁用，index 为数组序号，从 0 开始） |
| `POST` | `/config/llm_services/{index}/delete.json` | 删除指定服务 |
| `POST` | `/config/llm_services/{index}/set_default.json` | 设为默认服务（无请求体） |
| `POST` | `/config/llm_services/test.json` | 测试 LLM 服务连通性（支持已保存和未保存的配置） |

### 列表响应示例

```json
{
  "llm_services": [
    {
      "name": "qwen",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key": "sk-xxxxxxxxxxxx",
      "type": "openai-compatible",
      "model": "qwen-plus",
      "enable": true,
      "context_window_tokens": 131072,
      "reserve_output_tokens": 8192,
      "compact_trigger_ratio": 0.85,
      "compact_summary_max_tokens": 2048
    }
  ],
  "default_llm_server": "qwen"
}
```

### 连通性测试 API

**请求体**：通过 `mode` 字段区分测试已保存服务还是临时配置。

测试已保存的服务（按数组序号）：

```json
{
  "mode": "saved",
  "index": 0
}
```

测试未保存的临时配置：

```json
{
  "mode": "temp",
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-xxxx",
  "type": "deepseek",
  "model": "deepseek-chat"
}
```

**成功响应**：

```json
{
  "status": "ok",
  "message": "连接成功",
  "detail": {
    "model": "deepseek-chat",
    "response_text": "Hello!",
    "duration_ms": 523,
    "usage": {
      "prompt_tokens": 8,
      "completion_tokens": 2,
      "total_tokens": 10
    }
  }
}
```

**失败响应**：

```json
{
  "status": "error",
  "message": "认证失败：API Key 无效",
  "detail": {
    "error_type": "AuthenticationError",
    "raw_error": "Incorrect API key provided: sk-****xxxx"
  }
}
```

### 配置校验规则

| 字段 | 校验规则 |
|------|---------|
| `name` | 非空，不可与已有服务重名 |
| `base_url` | 非空，必须以 `http://` 或 `https://` 开头 |
| `api_key` | 非空 |
| `type` | 必须为 `LlmServiceType` 枚举值之一 |
| `model` | 非空 |
| `compact_trigger_ratio` | 0.0 ~ 1.0 |

---

## 前端交互

### 页面结构

在现有 `ModelsSettingsSection.vue` 中，将占位内容替换为真实数据：

1. **服务列表区域**：展示所有 LLM 服务卡片，每个卡片显示核心字段与操作按钮
2. **新增服务按钮**：打开新增表单弹窗

### 服务卡片交互

- 点击卡片可展开/编辑服务详情
- 启用/禁用通过开关控制
- "设为默认"按钮仅在已启用的非默认服务上出现
- 默认服务带有 ★ 标记
- "测试连接"按钮发起连通性测试，结果以 toast 或内联状态展示（成功：绿色 ✓ + 耗时；失败：红色 ✗ + 错误原因）
- 删除操作需二次确认

### 表单弹窗

- 新增和编辑共用同一表单组件
- 高级配置（Token 预算相关）默认折叠
- 表单内提供"测试连接"按钮，可在保存前验证配置可用性
- 保存时前端先做基本校验，再提交后端

---

## 验收标准

- [ ] Web Console 的"大模型服务管理"页面展示所有 LLM 服务配置，不再是占位内容。
- [ ] 可通过 Web Console 新增 LLM 服务配置，必填字段缺失时后端返回校验错误。
- [ ] 可通过 Web Console 编辑已有 LLM 服务配置（名称不可修改）。
- [ ] 可通过 Web Console 删除非默认的 LLM 服务。
- [ ] 可通过开关启用 / 禁用 LLM 服务。
- [ ] 可在已启用的服务中切换默认服务，切换后 Agent 下一次推理使用新默认服务。
- [ ] 切换默认服务后无需重启后端。
- [ ] 可对已保存的 LLM 服务发起连通性测试，成功时返回响应耗时和 token 统计，失败时返回具体错误原因。
- [ ] 可在新增服务表单中、保存前发起连通性测试，验证临时配置是否可用。
- [ ] 连通性测试不影响 Agent 运行状态，不写入消息历史或活动记录。
- [ ] API Key 在当前阶段以明文返回，后续版本再加密。
- [ ] 所有配置变更通过内存热更新 + 异步写回 `setting.json`，重启后配置不丢失。
- [ ] 页面不展示"调用策略"占位卡片。

---

## 使用说明

### 新增一个 LLM 服务

1. 打开 Web Console → 系统设置 → 大模型服务管理
2. 点击"新增服务"
3. 填写名称、Base URL、API Key、类型、模型
4. 可选展开"高级配置"调整 Token 预算参数
5. 点击"保存"

### 切换默认服务

1. 在服务列表中找到目标服务
2. 确认目标服务已启用
3. 点击"设为默认"
4. 前端显示成功提示，默认标记转移到新服务

### 禁用一个服务

1. 在服务列表中找到目标服务
2. 将启用开关关闭
3. 若该服务当前为默认服务，需先切换默认到其他服务，再禁用

### 测试 LLM 服务连通性

1. 在服务列表中，点击目标服务的"测试连接"按钮
2. 后端发送一个最小推理请求到目标服务
3. 成功：显示绿色提示，展示响应耗时和 token 用量
4. 失败：显示红色提示，展示具体错误原因（如认证失败、模型不存在、网络不通）
5. 新增服务时，也可在保存前点击"测试连接"预先验证配置
