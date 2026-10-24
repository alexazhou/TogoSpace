# 单元测试与集成测试的边界

本文补充 `case_design_guide.md` / `execution_architecture.md` 未覆盖的实践约束，重点是**测试该放哪一层**以及**如何避免同 Worker 内的跨用例污染**。

---

## 1. 一句话判据

> **只要测试需要真实服务、真实数据库、或会写进程级全局状态，它就是集成测试。**

放到 `tests/unit/` 的测试必须满足：

- 不调用任何 `xxxService.startup()`
- 不启动 ORM / 不建立真实数据库连接
- 不改写模块级可变全局（直接赋值或 `.clear()` 都算）
- 用 `monkeypatch` / `unittest.mock.patch` 的，天然满足上一条（框架负责回滚）

任一条件不满足，移到 `tests/integration/` 并继承 `ServiceTestCase`。

---

## 2. 为什么必须这样分

Worker 进程内，**数据库按测试类隔离，内存全局完全不隔离**：

| 维度 | 隔离单位 | 说明 |
|------|----------|------|
| SQLite | 测试类 | `ServiceTestCase.setup_class` 删库重建；类内多个用例**共享同一个 DB** |
| 内存全局 | 无 | 模块级变量在整个 Worker 生命周期内持续存在 |

所以类的边界就是唯一的重置点。一个不归属 `ServiceTestCase` 的纯 unit 文件，写入的任何模块级状态都会一路带到**同 Worker 的后续所有测试类**。

xdist 用 `--dist loadscope`，同一类固定在一个 Worker；但两个测试文件被分到同一个 Worker 是随机的，因此这类污染表现为**偶发失败**（跑单文件必过、跑全量偶尔挂）。

---

## 3. 需要留意的模块级全局

排查时的检查清单：

| 模块 | 全局状态 | 清空方式 |
|------|----------|----------|
| `agentService.core` | `_agents`（Agent 注册表） | `agentService.startup()` / `core._agents.clear()` |
| `roomService.core` | `_rooms`、`_rooms_by_id` | `roomService.startup()` / `shutdown()` |
| `funcToolService.core` | `_func_tools` | `funcToolService.startup()` / `shutdown()` |
| `skillService` | `_registry` | `skillService.startup()`（重建），无 shutdown |
| `configUtil.core` | `_cached_app_config`、`_cached_config_dir`、`_cached_preset_dir` | 无重置入口，需自行备份/恢复 |
| `updateService` | `_cached_result`、`_cached_at` | 用例内显式重置 |
| `messageBus` | 订阅者表 | `messageBus.shutdown()` |

**典型事故**：测试往 `_agents` 塞 `MagicMock` 后不清理，后续任何 `agentService.shutdown()` 都会执行 `asyncio.gather(*[a.close() ...])`，而 `MagicMock.close()` 不是 awaitable，直接抛 `TypeError: An asyncio.Future, a coroutine or an awaitable is required`。

---

## 4. start / shutdown 必须成对

`async_setup_class` 里启动的每个服务，`async_teardown_class` 都要关闭，否则状态会漏到下一个类。

推荐顺序（与生产启动顺序相反）：

```python
@classmethod
async def async_teardown_class(cls):
    await agentService.shutdown()      # 先停 Agent，避免它继续写库
    funcToolService.shutdown()
    roomService.shutdown()
    await persistenceService.shutdown()
    await ormService.shutdown()        # 最后关数据库
    await messageBus.shutdown()
```

仅在单个用例内启动的服务，在用例级 `teardown_method` 里关：

```python
def setup_method(self):
    scheduler.shutdown()

def teardown_method(self):
    roomService.shutdown()   # 用例内启动的，用例内收尾
```

注意 `agentService.shutdown()` 会调用 `Agent.close()`，如果注册表里混入了测试替身，必须先 `clear()` 再 shutdown。

---

## 5. 谁污染谁清理，不要给前序用例擦屁股

用例失败时应**如实失败并指名道姓**，不要在后续用例里做补救性清理：

```python
# 反例：后续用例替前序用例收拾状态
agent.inject_history_messages([])
agent.task_consumer.status = AgentStatus.IDLE

# 正例：断言前置条件成立，不满足就失败
await self.wait_agents_settled()
assert agent.task_consumer.status == AgentStatus.IDLE, f"前序用例遗留了运行中的 consumer: {name}"
```

补救代码会把「某个用例没走到确定结束点」这个真实缺陷藏起来，只在更晚、更不相关的地方以奇怪的方式暴露。

---

## 6. 等待确定性结束点，而不是等某个中间信号

调度器驱动的集成测试常见错误：等到「消息出现了」或「房间 IDLE」就结束用例。这两个都不是 turn 的结束点——consumer 协程可能仍在跑最后一个 turn。

turn 的确定结束点：

- 起点：`run_task_turn` 追加 `ROOM_TURN_BEGIN`
- 终点：finish 工具成功 → 对应 TOOL 条目打上 `ROOM_TURN_FINISH` → consumer 状态回到 `IDLE`

因此判据是「agent 处于 IDLE **且** history 中没有 active turn」：

```python
await self.wait_until(
    lambda: all(
        agent.status == AgentStatus.IDLE
        and not agent.task_consumer._turn_runner._history.has_active_turn()
        for agent in agentService.get_team_agents(team_id)
    ),
    timeout=3.0,
    message="agent turn 未在限时内结束",
)
```

建议在类上挂一个 autouse fixture，用例结束时统一断言调度器与 agent 状态已归于平静（只断言，不修复）。

---

## 7. 测试数据要真实落库

需要历史数据时，走真实写路径拿到真实主键：

```python
history = agent.task_consumer._turn_runner._history
await history.append_history_message(item)
```

不要用 `inject_history_messages()` 直接替换内存，再手工补 `item.id = 1` —— 假主键会掩盖依赖行 id 的逻辑（如 session 划分），也让测试与生产路径脱节。

同理，**不要在测试里删除其他用例的数据**（如 `delete_history_by_agent`）：同一类的用例共享数据库，删数据会破坏其他用例与后台任务的状态。

---

## 8. 目录级 conftest 处理全局缓存

`configUtil.load()` 会写进程级缓存，而配置加载类测试用的是临时目录。目录被清理后，后续测试会拿到指向已删除路径的陈旧配置。

这类「框架无法自动回滚的全局缓存」，在**目录级 conftest** 用 autouse fixture 备份/恢复，一次覆盖整个目录：

```python
# tests/unit/service/conftest.py
@pytest.fixture(autouse=True)
def restore_config_cache():
    saved = (config_core._cached_app_config, config_core._cached_config_dir, config_core._cached_preset_dir)
    yield
    (config_core._cached_app_config,
     config_core._cached_config_dir,
     config_core._cached_preset_dir) = saved
```

能回滚的（`monkeypatch`、`patch`）就别手写 `try/finally`。

---

## 9. 排查偶发失败

偶发失败先排除「自己的测试进程互相干扰」：

```bash
pgrep -fl "python.*-m pytest"      # 确认没有残留/并发的测试进程
```

多个 pytest 同时跑会共享 `/tmp/teamagent_tests_{worker_id}_{class_hash}.db`，日志甚至会被交错写入同一个文件，产生自相矛盾的汇总行——这类数据不能作为判断依据。

确认干净后用**确定性复现**代替反复全量：

```bash
# 把可疑的两个文件放在同一个进程里跑
.venv/bin/python3 -m pytest tests/unit/service/xxx/test_a.py tests/integration/yyy/test_b.py -q
```

能稳定复现，就说明是全局状态污染；再对照第 3 节的清单定位。
