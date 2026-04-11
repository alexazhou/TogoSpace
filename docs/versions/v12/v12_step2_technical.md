# V12: 大模型服务配置与动态切换 - 技术文档

## 1. 架构概览

V12 的核心变更是将"只读"的 LLM 服务配置，扩展为"可读写 + 运行时热更新"的管理能力。当前系统配置链路：

```text
setting.json  ──(启动时加载)──>  configUtil._cached_app_config.setting
                                         │
                                         └──>  llmService.infer() 每次推理时读取
                                                configUtil.get_app_config().setting.current_llm_service
```

V12 在此基础上增加写入路径：

```text
                                    ┌── configController (现有，只读)
                                    │     GET /config/frontend.json
                                    │     GET /config/directories.json
Web Console  ── HTTP ──>  route.py ─┤
                                    └── settingController (V12 新增，读写)
                                          GET  /config/llm_services/list.json
                                          POST /config/llm_services/create.json
                                          POST /config/llm_services/{index}/modify.json
                                          POST /config/llm_services/{index}/delete.json
                                          POST /config/llm_services/{index}/set_default.json
                                          POST /config/llm_services/test.json
                                                │
                                                ▼
                                         configUtil (扩展写入能力)
                                          ├─ 内存热更新: 修改 _cached_app_config.setting
                                          └─ 异步写回: _save_setting_to_file()
```

设计要点：

- **不引入新 Service 层**：LLM 服务配置的管理逻辑足够简单（CRUD + 校验），直接在 Controller 中调用 `configUtil` 的写入方法即可，不新增 `llmServiceConfigService`。
- **不引入 DB 存储**：配置仍保存在 `setting.json` 文件中，与当前行为一致。后续如需迁移到 DB，只需替换 `configUtil` 的持久化实现。
- **热更新零通知**：`llmService` 每次推理时都通过 `configUtil.get_app_config().setting.current_llm_service` 实时读取，修改内存缓存后即生效。

---

## 2. configUtil 扩展

### 2.1 新增写入方法

在 `src/util/configUtil.py` 中新增以下方法：

```python
def update_setting(mutator: Callable[[SettingConfig], None]) -> None:
    """原子性地修改内存中的 SettingConfig，然后异步写回文件。

    mutator 函数接收当前 SettingConfig，直接就地修改字段值。
    调用完成后自动触发 _save_setting_to_file()。
    """

def _save_setting_to_file() -> None:
    """将当前内存中的 SettingConfig 序列化后写回 setting.json。

    写入策略：先写临时文件再 rename，确保原子性。
    """
```

### 2.2 序列化策略

写回 `setting.json` 时需要注意：

- `SettingConfig` 使用 `ConfigDict(extra="ignore")`，不会保留原文件中的未知字段（如 `_comment`）
- 方案：首次加载时保留原始文件内容的"骨架"（注释等辅助字段），写回时将 `llm_services` 和 `default_llm_server` 字段以 **JSON 合并写回** 的方式更新
- 具体实现：读取原文件 JSON → 更新 `llm_services` / `default_llm_server` 字段 → 写回完整 JSON
- **精简序列化**：使用 `model_dump(exclude_unset=True)` 仅写入构造时显式传入的字段，保持配置文件精简。与 `exclude_defaults` 不同，`exclude_unset` 跟踪的是"字段是否被显式提供"，不会丢弃用户显式设置的等于默认值的字段（如 `"enable": true`）。
- **修改服务时保留 fields_set**：Pydantic v2 的 `model_fields_set` 仅在构造时记录，后续属性赋值不更新。因此修改服务时使用 **dict 合并重建**策略：`current_dict = service.model_dump(exclude_unset=True)` → `current_dict.update(updates)` → `LlmServiceConfig(**current_dict)`，确保新对象的 `model_fields_set` = 原始字段 ∪ 修改字段。

```python
def _save_setting_to_file() -> None:
    path = os.path.join(_cached_config_dir, "setting.json")

    # 读取原始 JSON（保留 _comment 等非模型字段）
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # 精简序列化：仅写入显式设置过的字段
    setting = _cached_app_config.setting
    raw["llm_services"] = [
        s.model_dump(exclude_unset=True) for s in setting.llm_services
    ]
    raw["default_llm_server"] = setting.default_llm_server

    # 原子写入
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp_path, path)
```

### 2.3 线程安全

- 当前系统是单进程单线程事件循环（Tornado + asyncio），所有 HTTP Handler 在同一个 event loop 中顺序执行
- `update_setting()` 是同步操作（修改内存 + 同步写文件），不存在并发竞争
- 若后续引入多进程，需考虑文件锁

---

## 3. Controller 设计

### 3.1 新增 `settingController.py`

位于 `src/controller/settingController.py`，遵循现有 Controller 模式（继承 `BaseHandler`，使用 `parse_request` / `return_json` / `return_with_error`）。

### 3.2 请求模型（Pydantic）

Create 接口直接复用 `configTypes.LlmServiceConfig` 作为请求模型，无需单独定义。`LlmServiceConfig` 已包含所有需要的字段和默认值，且 `type` 字段直接使用 `LlmServiceType` 枚举校验。

其余接口使用专用请求模型：

```python
class TestLlmServiceRequest(BaseModel):
    """支持两种模式：测试已保存服务或测试临时配置。"""
    mode: str  # "saved" | "temp"
    # mode="saved" 时使用
    index: int | None = None
    # mode="temp" 时使用
    base_url: str | None = None
    api_key: str | None = None
    type: str | None = None
    model: str | None = None
    extra_headers: dict[str, str] | None = None
```

Modify 接口不使用专用请求模型，而是直接从请求体获取原始 dict，过滤出 `LlmServiceConfig` 的已知可修改字段（排除 `name`），与现有服务做 dict 合并后重建 `LlmServiceConfig`。重建时 Pydantic 自动完成字段校验：

```python
body = json.loads(self.request.body)
known_fields = set(LlmServiceConfig.model_fields.keys()) - {"name"}
updates = {k: v for k, v in body.items() if k in known_fields}

current = service.model_dump(exclude_unset=True)
current.update(updates)
new_service = LlmServiceConfig(**current)
```
```

### 3.3 Handler 列表

| Handler | 方法 | 路径 | 说明 |
|---------|------|------|------|
| `LlmServiceListHandler` | GET | `/config/llm_services/list.json` | 返回全部服务列表 + default_llm_server |
| `LlmServiceCreateHandler` | POST | `/config/llm_services/create.json` | 新增服务 |
| `LlmServiceModifyHandler` | POST | `/config/llm_services/{index}/modify.json` | 修改服务（含启用/禁用，index 为数组序号） |
| `LlmServiceDeleteHandler` | POST | `/config/llm_services/{index}/delete.json` | 删除服务 |
| `LlmServiceSetDefaultHandler` | POST | `/config/llm_services/{index}/set_default.json` | 设为默认（无请求体） |
| `LlmServiceTestHandler` | POST | `/config/llm_services/test.json` | 连通性测试 |

### 3.4 校验逻辑

Controller 在调用 `configUtil.update_setting()` 前执行校验，使用现有的 `assertUtil` 断言工具：

**创建时校验**：
- `name` 非空
- `name` 不与已有服务重复
- `base_url` 非空，以 `http://` 或 `https://` 开头
- `api_key` 非空
- `type` 必须为有效的 `LlmServiceType` 枚举值
- `model` 非空

**修改时校验**：
- `index` 为合法数组下标（0 ≤ index < len(llm_services)）
- 传入的非空字段满足上述规则

**删除时校验**：
- `index` 为合法数组下标
- 不能删除当前默认服务（`default_llm_server` 指向的服务）

**切换默认时校验**：
- `index` 为合法数组下标
- 目标服务必须已启用（`enable=True`）

**修改时禁用默认服务的校验**：
- 如果修改的字段包含 `enable=False`，且目标服务是当前默认服务，返回错误

---

## 4. 连通性测试

### 4.1 测试实现

在 Controller 层（`LlmServiceTestHandler`）直接实现测试逻辑，不经过 `llmService` 模块，避免与 Agent 推理流程耦合：

```python
async def _test_llm_service(config: LlmServiceConfig) -> dict:
    """向目标 LLM 服务发送一个最小推理请求，验证连通性。"""
    provider = _TYPE_TO_PROVIDER.get(config.type)

    request = OpenAIRequest(
        model=config.model,
        messages=[OpenAIMessage.text(OpenaiApiRole.USER, "hi")],
        max_tokens=16,
    )

    start_time = time.monotonic()
    response = await llmApiUtil.send_request_non_stream(
        request,
        config.base_url,
        config.api_key,
        custom_llm_provider=provider,
        extra_headers=config.extra_headers,
    )
    duration_ms = int((time.monotonic() - start_time) * 1000)

    return {
        "model": config.model,
        "response_text": response.choices[0].message.content if response.choices else "",
        "duration_ms": duration_ms,
        "usage": response.usage.model_dump() if response.usage else None,
    }
```

### 4.2 请求解析

`TestLlmServiceRequest` 支持两种输入模式：

1. **按名称测试**：传 `name` 字段，从内存配置中查找对应的 `LlmServiceConfig`
2. **按临时配置测试**：传 `base_url` / `api_key` / `type` / `model` 四个必填字段，临时构造 `LlmServiceConfig`

优先级：若同时传了 `name` 和完整字段，以 `name` 为准。

### 4.3 错误分类

测试失败时，Controller 对异常信息进行分类返回：

```python
except Exception as e:
    error_type = type(e).__name__
    self.return_json({
        "status": "error",
        "message": str(e),
        "detail": {
            "error_type": error_type,
            "raw_error": str(e),
        }
    })
```

测试接口始终返回 HTTP 200（因为"测试失败"是业务层面的正常结果），通过 `status` 字段区分成功/失败。

---

## 5. 路由注册

在 `src/route.py` 中新增路由组：

```python
from controller import settingController

# LLM Service Config (V12)
(r"/config/llm_services/list.json",               settingController.LlmServiceListHandler),
(r"/config/llm_services/create.json",              settingController.LlmServiceCreateHandler),
(r"/config/llm_services/test.json",                settingController.LlmServiceTestHandler),
(r"/config/llm_services/(\d+)/modify.json",        settingController.LlmServiceModifyHandler),
(r"/config/llm_services/(\d+)/delete.json",        settingController.LlmServiceDeleteHandler),
(r"/config/llm_services/(\d+)/set_default.json",   settingController.LlmServiceSetDefaultHandler),
```

---

## 6. 测试策略

### 6.1 测试级别

V12 的后端接口测试采用 **API 集成测试**（`tests/api/` 目录），与现有 `test_config_api.py` / `test_config_controller/` 风格一致：

- 继承 `ServiceTestCase`
- 设置 `requires_backend = True`，`requires_mock_llm = True`
- 通过 `aiohttp.ClientSession` 发送 HTTP 请求到真实后端子进程
- 验证响应状态码、响应体字段、以及操作后的副作用（如列表查询变化）

### 6.2 测试文件

新增 `tests/api/test_llm_service_controller/` 目录：

```text
tests/api/test_llm_service_controller/
├── __init__.py
└── test.py
```

### 6.3 测试用例列表

| 测试方法 | 覆盖场景 |
|----------|---------|
| `test_list_llm_services` | 列表接口返回初始配置（含 mock 服务） |
| `test_create_llm_service` | 新增服务并验证列表更新 |
| `test_create_duplicate_name` | 重复名称创建返回 400 |
| `test_create_invalid_fields` | 缺少必填字段 / URL 格式错误返回 400 |
| `test_modify_llm_service` | 通过数组序号修改服务字段并验证生效 |
| `test_modify_invalid_index` | 序号越界返回 400 |
| `test_delete_llm_service` | 通过数组序号删除非默认服务并验证列表更新 |
| `test_delete_default_service` | 删除默认服务返回 400 |
| `test_set_enabled` | 启用/禁用服务并验证状态 |
| `test_disable_default_service` | 禁用默认服务返回 400 |
| `test_set_default` | 切换默认服务并验证 |
| `test_set_default_disabled` | 将禁用的服务设为默认返回 400 |
| `test_connectivity_by_name` | 按名称测试已保存服务（使用 mock LLM） |
| `test_connectivity_by_config` | 按临时配置测试（使用 mock LLM） |
| `test_connectivity_failure` | 测试不可达服务返回错误详情 |

### 6.4 配置写回验证

由于 API 测试使用独立的后端子进程和独立的配置目录，为验证"配置写回 setting.json"的正确性，测试在发起 create / modify / delete 请求后：

1. 通过 list 接口验证内存中配置已更新
2. 不直接读取子进程的 setting.json 文件（避免跨进程文件锁问题），而是信任"list 接口返回值 = 内存中的值 = 已写回的值"

如需独立验证文件写回，可在单元测试中直接调用 `configUtil.update_setting()` 并读取文件。

---

## 7. 现有代码影响分析

### 7.1 不受影响的模块

| 模块 | 说明 |
|------|------|
| `llmService` | 继续通过 `configUtil.get_app_config()` 读取，无需修改 |
| `agentService` | 不涉及 |
| `roomService` / `schedulerService` | 不涉及 |
| `persistenceService` | 不涉及 |
| `messageBus` / `wsController` | 不涉及（V12 不广播配置变更事件） |

### 7.2 需要修改的文件

| 文件 | 变更内容 |
|------|---------|
| `src/util/configUtil.py` | 新增 `update_setting()` / `_save_setting_to_file()` |
| `src/controller/settingController.py` | **新增**：7 个 Handler |
| `src/route.py` | 新增 7 条路由 |
| `tests/api/test_llm_service_controller/test.py` | **新增**：15 个测试用例 |

### 7.3 LlmServiceConfig 模型

现有 `LlmServiceConfig`（`src/util/configTypes.py`）已经包含所有需要的字段，**无需修改**：

```python
class LlmServiceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    base_url: str
    api_key: str
    type: LlmServiceType
    model: str = "qwen-plus"
    enable: bool = True
    extra_headers: dict[str, str] = Field(default_factory=_default_llm_extra_headers)
    context_window_tokens: int = 131072
    reserve_output_tokens: int = 8192
    compact_trigger_ratio: float = Field(default=0.85, ge=0.0, le=1.0)
    compact_summary_max_tokens: int = 2048
```

`SettingConfig` 的 `llm_services` 字段是 `list[LlmServiceConfig]`，可直接进行列表级别的增删改操作。

---

## 8. 实施步骤

按依赖顺序执行：

1. **扩展 configUtil**：新增 `update_setting()` + `_save_setting_to_file()`
2. **新建 settingController**：实现 7 个 Handler
3. **注册路由**：在 `route.py` 中添加 7 条路由
4. **编写测试**：在 `tests/api/test_llm_service_controller/` 下编写集成测试
5. **运行测试**：确保所有测试通过
