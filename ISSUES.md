# 项目问题分析报告

## 1. 命名规范与冗余问题
*   **服务目录冗余**：`src/service/` 下同时存在 `func_tool_service/` (空文件夹/仅缓存) 和 `funcToolService/` (实际代码)。代码中已统一使用 `camelCase`，应清理旧的 `snake_case` 目录。
*   **测试目录命名不匹配**：集成测试目录 `tests/integration/test_func_tool_service/` 内部代码实际在测试 `funcToolService`，且目录名未同步规范。
*   **残留空文件夹**：
    *   `tests/unit/test_tool_functions/` 仅含缓存。
    *   `tsp_driver_e2e_80a76ef8/` 为 E2E 测试残留的临时文件夹。

## 2. 类型检查错误 (Mypy)
运行 `./scripts/run_mypy.sh` 发现 13 处错误，主要包括：
*   **函数参数类型不匹配**：`normalize_team_config` 定义接收 `dict[str, Any]`，但多处调用传递了已是 `TeamConfig` 类型的变量。
*   **变量类型重用冲突**：`src/db.py` 中的 `applied` 变量在同一作用域内被先后赋予 `list[str]` 和 `list[Migration]` 类型，导致后续属性访问报错。
*   **TypedDict 嵌套定义不严谨**：`preset_rooms` 在赋值时存在类型推断不匹配。

## 3. 测试套件失败 (Pytest)
运行 `pytest` 发现大量 Error 和 Fail：
*   **环境冲突**：由于 `run/backend.pid` 文件存在且指向活跃进程，导致所有 API 测试因“拒绝启动第二个实例”而失败。
*   **数据库初始化问题**：持久化集成测试因 `_migrations` 表未初始化而崩溃，提示需要先运行 `migrate`。
*   **逻辑断言错误**：
    *   `tests/real/simple_chat/test_simple_chat.py`: `assert room.state.value == 'idle'` 失败，因为 `value` 是整数 (3)，应与枚举成员或常量比较。
*   **代码覆盖率缺失**：`src/controller/` 目录下所有文件的测试覆盖率为 0%（受后端启动失败影响）。

## 4. 工程化建议
*   **缺失 README.md**：根目录下缺少项目说明文档（虽然有 `CLAUDE.md`，但它是开发手册性质）。
*   **服务导入别名不统一**：`src/backend_main.py` 中有的服务使用 `as` 别名（如 `scheduler`, `chat_room`），有的直接使用原名，风格不一致。
