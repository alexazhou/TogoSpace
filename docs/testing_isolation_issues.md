# 测试隔离问题记录

## 目标

1. **删除 `areset_services` 方法**
   - 让各个测试用例自己初始化所需的 service
   - 避免基类提供过于通用的重置逻辑

2. **引入 fork 插件避免状态污染**
   - 参考 `/Volumes/PDATA/GitDB/trader` 项目
   - 为集成测试 (`integration/`) 和端到端场景测试 (`real/`) 添加进程隔离
   - 防止测试间状态污染

## 完成的工作

### 1. 删除 `areset_services` 和 `acleanup_services`

已从 `tests/base.py` 中删除以下方法：
- `areset_services()` - 异步重置 in-process service 状态
- `acleanup_services()` - 异步清理 in-process service 状态
- `reset_services()` - 同步壳
- `cleanup_services()` - 同步壳

### 2. 更新测试用例

更新了所有调用 `areset_services` 的测试文件：
- `tests/integration/test_persistence_service.py`
- `tests/integration/test_tool_functions/test.py`
- `tests/integration/test_room_service/test.py`
- `tests/integration/test_room_turn_logic/test.py`
- `tests/integration/test_agent_service/test_agent_service.py`
- `tests/integration/test_multi_agent/test.py`
- `tests/integration/test_persistence_restore.py`
- `tests/integration/test_agent_service/test_sdk_do_send.py`
- `tests/integration/test_func_tool_service/test.py`
- `tests/integration/test_scheduler_service/test.py`
- `tests/real/simple_chat/test_simple_chat.py`

改为直接在 `async_setup_class` 或测试方法中初始化所需的 service。

### 3. 安装 pytest 插件

```bash
.venv/bin/pip install pytest-forked
.venv/bin/pip install pytest-xdist
```

### 4. 尝试配置 fork 插件

在 `tests/conftest.py` 中尝试自动为 integration 和 real 测试添加 fork 标记：

```python
def pytest_collection_modifyitems(config, items):
    for item in items:
        path = str(item.fspath)
        if "/integration/" in path or "/real/" in path:
            item.add_marker("forked")
```

## 遇到的问题

### 1. macOS fork 与 objc 兼容性问题

在 macOS 上使用 `@pytest.mark.forked` 会导致以下错误：

```
objc[11183]: +[NSMutableString initialize] may have been in progress in another thread when fork() was called.
objc[11183]: +[NSMutableString initialize] may have been in progress in another thread when fork() was called. We cannot safely call it to ignore it in the fork() child process. Crashing instead.
```

这是因为 macOS 的 Objective-C 运行时在 fork 时存在已知的线程安全问题。

### 2. 测试间状态污染

即使单个测试运行正常，当多个测试一起运行时会出现状态污染问题。

#### 当前测试状态

```bash
.venv/bin/python -m pytest tests/integration/ tests/unit/ tests/real/ -v
```

```
========================= short test summary info ==========================
FAILED tests/integration/test_persistence_restore.py::TestPersistenceRestoreIntegration::test_restore_runtime_state_recovers_room_and_agent_history
FAILED tests/integration/test_scheduler_service/test.py::TestSchedulerRun::test_scheduler_runs_agent_on_turn_event
FAILED tests/integration/test_persistence_service.py::TestPersistenceService::test_restore_runtime_state_restores_room_history_and_read_index
FAILED tests/integration/test_persistence_service.py::TestPersistenceService::test_restore_runtime_state_restores_agent_history
======================== 4 failed, 105 passed in 7.52s ===========================
```

#### 状态污染来源

1. **agent_service 全局状态**
   - `src/service/agent_service/core.py` 中的模块级变量：
     ```python
     _agent_defs: Dict[str, dict] = {}
     _agents: Dict[str, "Agent"] = {}
     ```
   - 测试间共享同一个 `_agents` 和 `_agent_defs`

2. **persistence_service 数据库连接**
   - orm_service 的 session 状态可能残留
   - 数据库文件虽然使用 tmp_path 隔离，但连接状态未清理

3. **scheduler_service 全局状态**
   - `src/service/scheduler_service.py` 中的模块级变量：
     ```python
     _teams_config: List[dict] = []
     _running: Dict[str, asyncio.Task] = {}
     ```
   - `_stop_event` 状态可能残留

4. **room_service 全局状态**
   - `src/service/room_service.py` 中的 `_rooms` 字典

### 3. 尝试的解决方案

#### 方案 A: 在测试方法中添加清理

在 `test_persistence_service.py` 的 `setup_method` 中添加：

```python
def setup_method(self):
    room_service.shutdown()
    try:
        orm_service._session = None
        persistence_service._enabled = False
    except Exception:
        pass
```

问题：只能部分解决状态污染，agent_service 和 scheduler_service 的全局状态仍然污染。

#### 方案 B: 使用不同的 db 文件名

```python
db_path = tmp_path / "runtime_test_room.db"  # 独立文件名
```

问题：db 文件隔离了，但全局 service 状态仍然共享。

#### 方案 C: 在测试前调用 shutdown

```python
async def test_xxx(self):
    await agent_service.shutdown()
    # ...
```

问题：
- `agent_service.shutdown()` 是 async 函数，不能在同步的 `setup_method` 中调用
- 需要在每个测试方法中显式添加，代码冗余

## 待解决的选项

### 选项 1: 使用 xdist 的 subprocess 模式

pytest-xdist 使用 subprocess 而非 fork，可以避免 macOS 的 objc 问题。

```bash
.venv/bin/python -m pytest tests/integration/ tests/real/ -n auto --dist=loadscope
```

需要在 `pytest.ini` 中配置：
```ini
[pytest]
...
xfail_strict = true
# 为 integration 和 real 测试使用 xdist
```

### 选项 2: 为每个测试类添加独立的清理方法

创建一个统一的测试基类，在 `async_setup_class` 和 `async_teardown_class` 中处理所有相关 service 的清理。

### 选项 3: 重构 service 全局状态

将全局状态改为可重置的类实例，通过依赖注入传递给各测试。

## 当前文件状态

- `tests/base.py` - 已删除 `areset_services` 相关方法
- `tests/conftest.py` - 尝试的 fork 配置（暂未启用）
- `pytest.ini` - 基本配置

## 参考项目

`/Volumes/PDATA/GitDB/trader` 中的测试配置：
- 使用 `@pytest.mark.forked` 装饰器
- 在 Linux 环境下运行，没有 macOS objc 问题
