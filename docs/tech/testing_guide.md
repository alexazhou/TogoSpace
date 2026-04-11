# 测试系统指南

本文档详细介绍了 TeamAgent 项目的测试隔离机制、动态资源分配策略以及测试脚本的使用方法。

---

## 1. 核心隔离机制

为了支持高效的并行测试（Parallel Execution），系统在多个维度实现了彻底的隔离：

### 1.1 进程级隔离 (Process Isolation)
- **并发框架**: 使用 `pytest-xdist`。每个测试 Worker 运行在独立的 Python 进程中。
- **并行策略**: 默认按测试类（`--dist loadscope`）分发任务，同一测试类的用例在同一个 Worker 中按序执行，不同测试类可并行。
- **测试模式**: 后端进程在测试环境 (`TEAMAGENT_ENV=test`) 下跳过 PID 检查，允许多实例并行运行在不同端口。

### 1.2 数据库隔离 (Database Isolation)
- **动态路径**: 每个测试类都会得到独立的 SQLite 数据库文件；并行时再叠加 Worker 维度隔离。
    - 路径模板:
      - 并行模式: `/tmp/teamagent_tests_{worker_id}_{class_hash}.db`
      - 串行模式: `/tmp/teamagent_tests_{class_hash}.db`
- **配置注入**: `ServiceTestCase` 会在 `setup_class` 时通过 `mock.patch` 拦截全局配置加载器 `util.configUtil.load`。
    - **作用**: 强制将 `persistence.db_path` 指向该 Worker 的专属路径。
    - **一致性**: 确保当前测试进程、内部 Service 以及通过 `subprocess` 启动的后端进程都读写同一个隔离的数据库。

### 1.3 端口隔离 (Network Port Isolation)
- **自动偏移**: 基于 Worker ID 计算端口偏移量，避免多进程抢占。
    - **后端端口**: `18080 + worker_offset`
    - **Mock LLM 端口**: `19876 + worker_offset`
- **生命周期**: 在 `setup_class` 启动服务前会调用 `_wait_port_released` 确保端口清理干净。

---

## 2. 状态重置与清理

虽然 Worker 之间是完全隔离的，但在同一个 Worker 内，为了保证测试类之间的干净状态：

- **类级重置 (`setup_class`)**:
    1. 停止并移除上一个类的配置 Patch。
    2. 物理删除当前 Worker 专属的数据库文件。
    3. 运行数据库迁移脚本（`migrate_database`）重建 Schema。
    4. 启动该类所需的外部依赖（Backend, MockLLM）。
- **Service 级重置**:
    - 集成测试通常在 `async_setup_class` 中显式调用 `service.startup()`，并在 `async_teardown_class` 中调用 `shutdown()`，清空内存中的单例状态（如 `messageBus` 订阅者、`roomService` 缓存等）。

---

## 3. 测试脚本使用 (`run_tests.sh`)

项目提供了一个统一的入口脚本 `scripts/run_tests.sh`，封装了复杂的 pytest 参数。

### 3.1 常用命令

| 场景 | 命令 | 说明 |
|------|------|------|
| **快速测试 (推荐)** | `./scripts/run_tests.sh` | 默认并行运行，关闭覆盖率，速度最快。 |
| **覆盖率测试** | `./scripts/run_tests.sh --cov` | 开启覆盖率分析，生成终端报告及 XML 文件。 |
| **串行调试** | `./scripts/run_tests.sh --serial` | 禁用并行，适合排查复杂的竞争问题。 |
| **运行特定文件** | `./scripts/run_tests.sh path/to/test.py` | 仅运行指定文件。 |
| **关键字过滤** | `./scripts/run_tests.sh -k "keyword"` | 运行名称匹配关键字的用例。 |

### 3.2 进阶用法
脚本支持透传所有 `pytest` 原生参数：
```bash
# 运行 unit 测试，显示详细输出，并在第一个失败处停止
./scripts/run_tests.sh tests/unit -v -x
```

---

## 4. 开发建议

1. **本地开发**: 优先使用默认模式 `./scripts/run_tests.sh`，反馈最快。
2. **CI 流程**: 使用 `./scripts/run_tests.sh --cov` 以确保代码质量并收集合并覆盖率。
3. **断点调试**: 如果需要在测试中使用 `breakpoint()` 或 `pdb`，必须加上 `--serial` 参数，因为并行模式下标准输入会被重定向，无法交互。
4. **数据库查看**: 如果某个 Worker 的测试失败，可以在 `/tmp/` 目录下找到对应的 `.db` 文件进行排查。
