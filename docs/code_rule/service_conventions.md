# Service 模块规范

## 基本原则

每个 service 是一个 **Python 模块**，不是类。用模块级私有变量（`_` 前缀）维护状态，通过模块级函数对外暴露接口。

## 文件结构约定

```
imports

模块级私有变量（_xxx）

startup()       ← 生命周期：初始化
业务函数 ...    ← 核心逻辑
shutdown()      ← 生命周期：清理（必须放在文件最后）
```

## 生命周期方法

每个 service 必须实现两个生命周期方法：

| 方法 | 位置 | 职责 |
|------|------|------|
| `startup(...)` | 文件顶部（业务函数之前） | 初始化模块状态，可接收配置参数 |
| `shutdown()` | 文件末尾（最后一个函数） | 清空所有模块状态，无参数，无返回值 |

### 各 service 签名

| 模块 | startup 签名 |
|------|-------------|
| `message_bus` | `startup()` |
| `llm_service` | `startup(api_key: str, base_url: str)` |
| `func_tool_service` | `startup()` |
| `agent_service` | `startup()` |
| `room_service` | `startup()` |
| `scheduler_service` | `startup(teams_config: list)` |

## main.py 中的调用顺序

startup 按依赖顺序调用，shutdown 在 `finally` 块中逆序调用：

```python
# 启动（依赖顺序）
message_bus.startup()
llm_service.startup(api_key=..., base_url=...)
func_tool_service.startup()
agent_service.startup()
room_service.startup()
scheduler_service.startup(teams_config=...)

# 关闭（finally 块，逆序）
scheduler_service.shutdown()
agent_service.shutdown()
func_tool_service.shutdown()
room_service.shutdown()
llm_service.shutdown()
message_bus.shutdown()
```

## 测试中的用法

`tests/base.py` 的 `ServiceTestCase` 在每个测试方法的 setup/teardown 中统一调用：

```python
def setup_method(self):
    message_bus.startup()
    # 各服务 shutdown() 用于重置状态（替代 startup 前的残留）
    room_service.shutdown()
    agent_service.shutdown()
    func_tool_service.shutdown()
    scheduler_service.shutdown()

def teardown_method(self):
    scheduler_service.shutdown()
    func_tool_service.shutdown()
    agent_service.shutdown()
    room_service.shutdown()
    message_bus.shutdown()
```

> 注意：测试中 `shutdown()` 也用于 setup 阶段的状态重置，而非仅在清理时调用。
