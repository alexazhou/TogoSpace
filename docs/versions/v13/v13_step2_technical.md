# V13: 快速初始化引导 - 技术文档

## 1. 架构概览

V13 的核心变更是在现有 LLM 服务配置管理能力基础上，增加**初始化状态检测**与**快速初始化引导**机制。整体流程如下：

```text
setting.json  ──(启动时加载)──>  configUtil._cached_app_config.setting
         │                                    │
         │                                    ├── llm_services 为空或全部禁用?
         │                                    │     └─> initialized = false
         │                                    │
         │                                    └── 至少有一个已启用服务?
         │                                    │     └─> initialized = true
         │                                    │
Web Console  ── HTTP ──>  route.py ────────────┤
                                    │
                                    ├── configController (现有)
                                    │     GET /config/frontend.json
                                    │     GET /config/directories.json
                                    │
                                    ├── settingController (V12 已有)
                                    │     GET  /config/llm_services/list.json
                                    │     POST /config/llm_services/create.json
                                    │     POST /config/llm_services/test.json
                                    │     ...
                                    │
                                    ├── systemController (V13 新增)
                                    │     GET /system/status.json          # 系统状态（含初始化状态）
                                    │
                                    └── initController (V13 新增)
                                          POST /config/quick_init.json      # 快速初始化保存
                                                │
                                                ▼
                                         configUtil
                                          ├─ 内存热更新: 修改 _cached_app_config.setting
                                          └─ 异步写回: _save_setting_to_file()
                                                │
                                                ▼
                                         initialized 状态更新为 true
```

设计要点：

- **复用 V12 能力**：快速初始化的保存逻辑复用 V12 的 `configUtil.update_setting()`，连通性测试复用 V12 的 `/config/llm_services/test.json` 接口。
- **不引入复杂状态机**：`initialized` 状态由 `llm_services` 配置动态推导，不持久化独立字段。
- **前端主导引导流程**：弹窗触发、表单交互、跳过逻辑均在前端实现，后端仅提供状态查询与配置保存接口。

---

## 2. 初始化状态检测

### 2.1 状态定义

`initialized` 状态由 `llm_services` 配置动态计算，不作为独立字段持久化：

| 条件 | initialized | 说明 |
|------|-------------|------|
| `llm_services` 数组为空 | `false` | 无任何服务配置 |
| `llm_services` 非空，但全部 `enable=False` | `false` | 有配置但全部禁用 |
| `llm_services` 非空，至少一个 `enable=True` | `true` | 有可用服务 |

### 2.2 状态计算

在 `configUtil` 中新增方法：

```python
def is_initialized() -> bool:
    """判断系统是否已完成 LLM 服务初始化配置。"""
    setting = get_app_config().setting
    if not setting.llm_services:
        return False
    # 至少有一个已启用的服务
    return any(service.enable for service in setting.llm_services)
```

### 2.3 系统状态接口

`initialized` 状态作为系统状态接口的一个字段返回，而非独立接口。新增 `GET /system/status.json`，返回包含初始化状态在内的系统运行状态：

- `initialized`：布尔值，表示 LLM 服务是否已配置
- `message`：未初始化时的提示文案（已初始化时为 null）
- `default_llm_server`：已初始化时返回当前默认服务名称

此接口可后续扩展，添加更多系统状态字段（如运行时长、活跃 Team 数量等）。

---

## 3. Controller 设计

### 3.1 新增 `systemController.py` 与 `initController.py`

**systemController.py**：位于 `src/controller/systemController.py`，提供系统状态查询接口，遵循现有 Controller 模式。

**initController.py**：位于 `src/controller/initController.py`，提供快速初始化保存接口。

### 3.2 请求模型

快速初始化请求体仅需三个必要字段：`base_url`（API 地址）、`api_key`（鉴权密钥）、`model`（模型名称）。

### 3.3 Handler 列表

| Handler | Controller | 方法 | 路径 | 说明 |
|---------|------------|------|------|------|
| `SystemStatusHandler` | systemController | GET | `/system/status.json` | 返回系统状态（含初始化状态） |
| `QuickInitHandler` | initController | POST | `/config/quick_init.json` | 快速初始化保存配置 |

### 3.4 快速初始化逻辑

`QuickInitHandler` 处理流程：

1. **校验字段**：`base_url` 非空且以 `http://` 或 `https://` 开头；`api_key` 非空；`model` 非空
2. **构造完整配置**：自动填充默认值
   ```python
   new_service = LlmServiceConfig(
       name="default",
       base_url=request.base_url,
       api_key=request.api_key,
       type=LlmServiceType.OPENAI_COMPATIBLE,
       model=request.model,
       enable=True,
   )
   ```
3. **调用 configUtil.update_setting()**：
   ```python
   configUtil.update_setting(lambda s: (
       s.llm_services.append(new_service),
       setattr(s, "default_llm_server", "default")
   ))
   ```
4. **返回成功响应**

### 3.5 校验逻辑

使用 `assertUtil` 断言：

```python
# base_url 校验
assertNotNull(request.base_url, "API 地址不能为空")
assertTrue(
    request.base_url.startswith("http://") or request.base_url.startswith("https://"),
    "API 地址必须以 http:// 或 https:// 开头"
)

# api_key 校验
assertNotNull(request.api_key, "API Key 不能为空")

# model 校验
assertNotNull(request.model, "模型名称不能为空")
```

---

## 4. 连通性测试

### 4.1 复用 V12 接口

快速初始化的连通性测试复用 V12 的 `/config/llm_services/test.json` 接口，使用 `mode: "temp"` 模式：

前端请求示例：

```json
{
    "mode": "temp",
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-xxxx",
    "type": "openai-compatible",
    "model": "gpt-4o"
}
```

### 4.2 测试时序

```text
用户填写表单 ──> 点击"测试连接" ──> 前端调用 /config/llm_services/test.json
                                      │
                                      ├── 成功: 显示绿色提示 + 耗时
                                      │
                                      └── 失败: 显示红色提示 + 错误原因
                                      │
用户点击"完成" ──> 前端调用 /config/quick_init.json ──> 保存配置
```

**注意**：前端在调用 `quick_init` 前应确保测试已通过，但后端不强制要求（允许用户跳过测试直接保存）。

---

## 5. 路由注册

在 `src/route.py` 中新增路由组：

```python
from controller import systemController, initController

# System Status (V13)
(r"/system/status.json",      systemController.SystemStatusHandler),

# Quick Init (V13)
(r"/config/quick_init.json",  initController.QuickInitHandler),
```

---

## 6. 前端交互设计

### 6.1 弹窗触发时序

```text
用户打开 Web Console
    │
    ├── 前端调用 GET /system/status.json
    │
    ├── response.initialized === false ?
    │       │
    │       └── 是: 显示"快速初始化"弹窗（模态，遮罩背景）
    │       │
    │       └── 否: 正常进入主界面
```

### 6.2 弹窗组件结构

在 `frontend/src/components/` 下新增 `QuickInitModal.vue` 组件，包含以下功能：

- **标题与说明**：显示"快速初始化"标题及简短说明文案
- **三个必填输入框**：API 地址、API Key（密码类型）、模型名称，每个输入框附带提示信息（如常见服务商示例）
- **测试连接按钮**：点击后调用 `/config/llm_services/test.json` 验证配置可用性，显示测试结果（成功显示绿色提示+耗时，失败显示红色提示+错误原因）
- **底部按钮组**："跳过"按钮关闭弹窗但不改变状态；"完成"按钮在测试成功后可点击，调用 `/config/quick_init.json` 保存配置

### 6.3 状态管理

在 App.vue 或全局状态中维护初始化状态：

- `initialized`：布尔值或 null（null 表示未查询），从系统状态接口获取
- `showQuickInitModal`：布尔值，控制弹窗显示

页面加载时调用 `/system/status.json`，根据返回的 `initialized` 字段决定是否显示弹窗。

### 6.4 跳过逻辑

点击"跳过"按钮时：
- 关闭弹窗（`showQuickInitModal = false`）
- 不改变 `initialized` 状态，下次访问 Web Console 时弹窗仍会自动出现

---

## 7. TUI 兼容

### 7.1 状态查询

TUI 启动时通过系统状态接口查询初始化状态：

调用 `GET /system/status.json`，从返回的 `initialized` 字段判断是否已配置。

### 7.2 未初始化提示

在 TUI 主界面或启动阶段，若 `initialized === false`，显示提示信息：

```text
┌─────────────────────────────────────────────┐
│ ⚠ 当前未配置大模型服务                       │
│                                             │
│ 请通过以下方式完成配置：                      │
│ 1. 手动编辑 ~/.team_agent/setting.json      │
│ 2. 通过 Web Console 完成配置                 │
│                                             │
│ Web Console 地址：http://127.0.0.1:8080     │
└─────────────────────────────────────────────┘
```

### 7.3 不阻塞启动

TUI 在未初始化状态下仍可正常启动和展示界面，但：
- Agent 相关功能不可用（显示未配置提示）
- 可浏览房间列表、查看历史消息等非推理功能

---

## 8. 测试策略

### 8.1 测试级别

V13 的后端接口测试采用 **API 集成测试**（`tests/api/` 目录），与现有 V12 测试风格一致。

### 8.2 测试文件

新增 `tests/api/test_system_controller/` 目录（系统状态接口测试）：

```text
tests/api/test_system_controller/
├── __init__.py
└── test.py
```

新增 `tests/api/test_init_controller/` 目录（快速初始化接口测试）：

```text
tests/api/test_init_controller/
├── __init__.py
└── test.py
```

### 8.3 测试用例列表

**系统状态接口测试（test_system_controller/test.py）**：

| 测试方法 | 覆盖场景 |
|----------|---------|
| `test_system_status_initialized_false` | 配置为空时 `initialized: false` |
| `test_system_status_all_disabled` | 全部禁用时 `initialized: false` |
| `test_system_status_initialized_true` | 有已启用服务时 `initialized: true` |
| `test_system_status_with_default_llm` | 已初始化时返回 `default_llm_server` |

**快速初始化接口测试（test_init_controller/test.py）**：

| 测试方法 | 覆盖场景 |
|----------|---------|
| `test_quick_init_success` | 快速初始化保存成功 |
| `test_quick_init_invalid_url` | URL 格式错误返回 400 |
| `test_quick_init_missing_fields` | 缺少必填字段返回 400 |
| `test_quick_init_adds_service` | 保存后 `llm_services` 包含新服务 |
| `test_quick_init_sets_default` | 保存后 `default_llm_server` 为 `"default"` |
| `test_quick_init_updates_initialized` | 保存后系统状态中 `initialized` 变为 `true` |

### 8.4 测试配置场景

测试需覆盖不同初始配置状态：

1. **空配置**：`setting.json` 中 `llm_services = []`
2. **全禁用配置**：`llm_services` 非空但全部 `enable = false`
3. **已配置**：至少一个 `enable = true` 的服务

---

## 9. 现有代码影响分析

### 9.1 不受影响的模块

| 模块 | 说明 |
|------|------|
| `llmService` | 继续通过 `configUtil.get_app_config()` 读取，无需修改 |
| `agentService` | 不涉及 |
| `roomService` / `schedulerService` | 不涉及 |
| `persistenceService` | 不涉及 |
| `messageBus` / `wsController` | 不涉及 |
| `settingController` (V12) | 复用其测试接口，无需修改 |

### 9.2 需要修改的文件

| 文件 | 变更内容 |
|------|---------|
| `src/util/configUtil.py` | 新增 `is_initialized()` 方法 |
| `src/controller/systemController.py` | **新增**：`SystemStatusHandler` |
| `src/controller/initController.py` | **新增**：`QuickInitHandler` |
| `src/route.py` | 新增 2 条路由 |
| `frontend/src/components/QuickInitModal.vue` | **新增**：弹窗组件 |
| `frontend/src/App.vue` 或全局状态 | 新增初始化状态检查逻辑 |
| `tui/api_client.py` | 新增系统状态查询方法 |
| `tui/app.py` | 新增未初始化提示展示 |
| `tests/api/test_system_controller/test.py` | **新增**：4 个系统状态测试用例 |
| `tests/api/test_init_controller/test.py` | **新增**：6 个快速初始化测试用例 |

---

## 10. 实施步骤

按依赖顺序执行：

1. **扩展 configUtil**：新增 `is_initialized()` 方法
2. **新建 systemController**：实现 `SystemStatusHandler`
3. **新建 initController**：实现 `QuickInitHandler`
4. **注册路由**：在 `route.py` 中添加 2 条路由
5. **编写后端测试**：在 `tests/api/test_system_controller/` 和 `tests/api/test_init_controller/` 下编写集成测试
6. **实现前端弹窗**：新增 `QuickInitModal.vue` 组件
7. **集成前端状态检查**：在 App.vue 或全局状态中添加初始化状态查询
8. **TUI 状态检查**：在 `tui/api_client.py` 新增系统状态查询方法
9. **TUI 提示展示**：在 `tui/app.py` 中添加未初始化提示
10. **运行测试**：确保所有测试通过

---

## 11. API 规范

### 11.1 GET /system/status.json

系统状态接口，返回包含初始化状态在内的系统运行状态。

**请求**：无参数

**响应（未初始化）**：

```json
{
    "initialized": false,
    "message": "当前未配置大模型服务"
}
```

**响应（已初始化）**：

```json
{
    "initialized": true,
    "default_llm_server": "qwen"
}
```

**HTTP 状态码**：200

**扩展预留**：此接口可后续扩展，添加更多系统状态字段（如 `version`、`uptime`、`active_teams` 等）。

### 11.2 POST /config/quick_init.json

**请求体**：

```json
{
    "base_url": "https://api.openai.com/v1",
    "api_key": "sk-xxxx",
    "model": "gpt-4o"
}
```

**成功响应**：

```json
{
    "status": "ok",
    "message": "配置保存成功",
    "detail": {
        "name": "default",
        "model": "gpt-4o"
    }
}
```

**失败响应（校验错误）**：

```json
{
    "status": "error",
    "message": "API 地址必须以 http:// 或 https:// 开头"
}
```

**HTTP 状态码**：200（通过 `status` 字段区分成功/失败）

---

## 12. 配置文件影响

### 12.1 快速初始化后的 setting.json

快速初始化成功后，`setting.json` 新增以下内容：

```json
{
    "llm_services": [
        {
            "name": "default",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-xxxx",
            "type": "openai-compatible",
            "model": "gpt-4o",
            "enable": true
        }
    ],
    "default_llm_server": "default"
}
```

### 12.2 字段默认值说明

快速初始化自动填充的字段：

| 字段 | 值 | 说明 |
|------|-----|------|
| `name` | `"default"` | 固定名称 |
| `type` | `"openai-compatible"` | 默认类型 |
| `enable` | `true` | 默认启用 |
| 其他高级字段 | 使用 `LlmServiceConfig` 默认值 | 如 `context_window_tokens` 等 |