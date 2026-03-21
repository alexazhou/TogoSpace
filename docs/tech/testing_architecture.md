# 测试架构设计文档

本文档记录了 TeamAgent 项目的测试体系架构、Mock 逻辑、目录拆分原则以及配置加载机制。

---

## 1. 测试分层设计

项目采用分层测试策略，根据测试粒度和依赖范围，将测试分为四个核心类别：

### 1.1 单元测试 (Unit Tests)
- **目录**: `tests/unit/`
- **目标**: 验证纯逻辑函数、工具函数、算法逻辑。
- **特点**: 不依赖外部服务，不启动后台进程，运行速度极快。
- **示例**: 配置归一化逻辑、时间格式化、计算器工具函数。

### 1.2 集成测试 (Integration Tests)
- **目录**: `tests/integration/`
- **目标**: 验证 Service 层之间的协同工作、状态机流转和消息总线行为。
- **特点**: 
    - 通常在进程内运行（In-Process）。
    - 涉及 `agent_service`、`room_service`、`scheduler` 的交互。
    - 频繁使用 `patch_infer` 注入剧本式 Mock 响应。
- **示例**: 多 Agent 对话轮次推进、持久化状态恢复、Tool Call 执行链路。

### 1.3 API 测试 (API Tests)
- **目录**: `tests/api/`
- **目标**: 验证 HTTP/WebSocket 接口的协议正确性、序列化与反序列化逻辑。
- **特点**: 
    - 启动真实的后端子进程 (`main.py`)。
    - 启动外部 Mock LLM 服务。
    - 使用 `aiohttp` 作为真实客户端发起请求。
- **示例**: `GET /rooms` 返回值结构、WebSocket 实时推送、POST 消息入库验证。

### 1.4 场景测试 (Real/E2E Tests)
- **目录**: `tests/real/`
- **目标**: 验证完整的端到端业务剧本。
- **特点**: 通常基于真实的 service 组合，使用 Mock LLM 控制对话内容，模拟用户真实使用场景。

---

## 2. Mock 策略

项目提供了两种层级的 Mock 方案，以平衡开发效率与验证深度：

### 2.1 进程内拦截 (In-Process Mocking)
主要用于 **集成测试**。通过基类 `ServiceTestCase.patch_infer` 接口实现。

- **原理**: 使用 `unittest.mock.patch` 拦截 `llm_service.infer` 的底层调用。
- **优势**: 
    - **极简数据结构**: 允许传入简化字典 `{"content": "..."}` 或 `{"tool_calls": [...]}`，基类自动转换为复杂的 `LlmApiMessage` 对象。
    - **逻辑灵活**: 支持按顺序返回响应序列，或传入自定义 `handler` 函数根据 Agent 身份动态决定回复。
    - **性能高**: 无网络开销，不启动额外进程。

### 2.2 协议级模拟 (Out-of-Process Mocking)
主要用于 **API 测试** 和 **后端子进程测试**。通过 `MockLLMServer` 实现。

- **原理**: 启动一个独立的 Tornado 服务（默认端口 19876），模拟 OpenAI/Anthropic API 行为。
- **优势**: 
    - **全链路验证**: 验证 `llm_api_util` 客户端的 HTTP 配置、请求头、重试逻辑。
    - **真实交互**: 与后端子进程完全解耦，模拟最真实的外部依赖环境。
    - **支持队列**: 通过 `SetResponseHandler` 动态推入预设的回复剧本。

---

## 3. 测试基类 `ServiceTestCase`

所有非纯单元测试的测试类都应继承自 `tests.base.ServiceTestCase`。它提供了以下统一能力：

### 3.1 外部依赖管理
通过类属性声明式开启依赖：
- `requires_backend = True`: 自动在 `setup_class` 启动后端子进程，并在 `teardown_class` 关闭。
- `requires_mock_llm = True`: 自动启动/关闭 `MockLLMServer`。

### 3.2 核心工具接口
- `patch_infer(responses=None, handler=None)`: 上下文管理器，用于注入 Mock 推理结果。
- `normalize_to_mock(data)`: 将简化的核心数据字典转换为 `MagicMock` 对象。
- `set_mock_response(response)`: 向外部 `MockLLMServer` 发送预设响应。

---

## 4. 配置文件加载逻辑

测试环境下的配置文件加载遵循以下优先级和路径规则：

1. **默认公共配置**: `tests/config/`。包含通用的 LLM Mock 地址、基础 Team 和 Agent 定义。
2. **自定义专用配置**: 若测试类设置 `use_custom_config = True`，基类会自动定位到该测试文件所在目录下的 `config/` 子目录。
    - **路径选择**: `os.path.dirname(test_file) + "/config"`。
    - **用途**: 适用于需要特定房间结构、特定 Prompt 或特殊持久化路径的测试场景。
3. **加载机制**: 后端子进程启动时，通过 `--config-dir` 参数强制指定目标配置目录，确保测试环境与生产配置物理隔离。

---

## 5. 隔离性保证

为了确保测试间互不干扰（特别是 Service 层的单例状态），项目采取了以下措施：

- **进程隔离**: 对涉及全局状态的测试类，使用 `@pytest.mark.forked` 在独立子进程中运行每个类。
- **显式重置**: 在 `ServiceTestCase.setup_method` 中，统一调用所有 service 的 `shutdown()` 方法清空内存数据，并重新调用 `startup()`。
- **端口随机化**: 后端进程每次启动使用 `_find_free_port()` 探测可用端口，支持并发运行多组测试。
