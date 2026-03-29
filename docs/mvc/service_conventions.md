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

补充约定：

- `startup()` 必须出现在文件中靠前位置，位于所有业务函数和私有辅助函数之前。
- 私有辅助函数（如 `_load_xxx()`、`_import_xxx()`）也视为业务函数的一部分，应放在 `startup()` 之后。
- `shutdown()` 必须保持为文件最后一个函数。

## 生命周期方法

每个 service 必须实现两个生命周期方法：

| 方法 | 位置 | 职责 |
|------|------|------|
| `startup(...)` | 文件顶部（业务函数之前） | 初始化模块状态，可接收配置参数 |
| `shutdown()` | 文件末尾（最后一个函数） | 清空所有模块状态，无参数，无返回值 |

### 各 service 签名

| 模块 | startup 签名 |
|------|-------------|
| `messageBus` | `startup()` |
| `llmService` | `startup(api_key: str, base_url: str)` |
| `funcToolService` | `startup()` |
| `agentService` | `startup()` |
| `roomService` | `startup()` |
| `schedulerService` | `startup(teams_config: list)` |

## main.py 中的调用顺序

startup 按依赖顺序调用，shutdown 在 `finally` 块中逆序调用：

```python
# 启动（依赖顺序）
messageBus.startup()
llmService.startup(api_key=..., base_url=...)
funcToolService.startup()
agentService.startup()
roomService.startup()
schedulerService.startup(teams_config=...)

# 关闭（finally 块，逆序）
schedulerService.shutdown()
agentService.shutdown()
funcToolService.shutdown()
roomService.shutdown()
llmService.shutdown()
messageBus.shutdown()
```

## 测试中的用法

`tests/base.py` 的 `ServiceTestCase` 在每个测试方法的 setup/teardown 中统一调用：

```python
def setup_method(self):
    messageBus.startup()
    # 各服务 shutdown() 用于重置状态（替代 startup 前的残留）
    roomService.shutdown()
    agentService.shutdown()
    funcToolService.shutdown()
    schedulerService.shutdown()

def teardown_method(self):
    schedulerService.shutdown()
    funcToolService.shutdown()
    agentService.shutdown()
    roomService.shutdown()
    messageBus.shutdown()
```

> 注意：测试中 `shutdown()` 也用于 setup 阶段的状态重置，而非仅在清理时调用。
