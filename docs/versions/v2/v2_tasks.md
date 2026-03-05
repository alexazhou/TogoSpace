# V2: 多 Agent 单房间聊天 - 开发任务表

## 任务概览

V2 在 V1 基础上扩展，核心变更为：新增 `Scheduler` 调度类、新增 V2 配置文件和 Charlie 的提示词、修改 `main.py` 以支持多 Agent。除 `main.py` 外，所有 V1 核心组件直接复用，无需修改。

共 4 个任务，按依赖关系排序。

---

## 任务列表

### 任务 1: 创建 Charlie 提示词和 V2 配置文件

**描述**: 新增第三个 Agent（Charlie）的 system prompt，并创建 V2 的配置文件

**依赖**: 无

**子任务**:
- [ ] 创建 `resource/prompts/charlie_system.md`，定义哲学教授角色和性格
- [ ] 在提示词中使用 `{participants}` 占位符，运行时动态注入参与者名称
- [ ] 创建 `config/agents_v2.json`，包含 alice、bob、charlie 三个 Agent 的配置
- [ ] 配置 `max_turns` 为 6（每人各说 2 次）

**验收标准**:
- `charlie_system.md` 内容完整，包含性格特点和说话风格
- `agents_v2.json` 格式正确，能被 json.load 解析
- 包含 3 个 Agent 配置，每个有 name、prompt_file、model 字段
- 包含 chat_room 和 max_turns 配置

---

### 任务 2: 实现 Scheduler 类

**描述**: 在 `src/core/scheduler.py` 中实现多 Agent 调度器

**依赖**: 无（`Agent`、`ChatRoom` 等类直接复用）

**文件**: `src/core/scheduler.py`（新建）

**子任务**:
- [ ] 实现 `Scheduler.__init__`，接收 agents 列表、chat_room、max_turns
- [ ] 直接调用 `utils/api.py` 中的工具函数，无需注入 API 客户端
- [ ] 实现 `run` 异步方法，核心调度逻辑：
  - 使用 `(turn - 1) % len(agents)` 循环选取当前发言 Agent
  - 获取聊天室上下文消息
  - 调用 `generate_with_function_calling` 生成回复
  - 将回复写入聊天室（通过 function calling 的 `send_chat_msg`，或直接 `add_message`）
  - 记录日志
- [ ] 处理异常并记录错误日志

**验收标准**:
- `Scheduler` 能正确初始化
- `run()` 按轮次循环调用每个 Agent
- 2 个 Agent 时行为与 V1 一致
- 3 个及以上 Agent 时，每轮只有 1 个 Agent 发言
- 异常时能打印错误并安全退出

---

### 任务 3: 修改 main.py

**描述**: 修改主程序，加载 V2 配置，动态创建 Agent，使用 Scheduler 驱动对话

**依赖**: 任务 1、任务 2

**文件**: `src/main.py`（修改）

**子任务**:
- [ ] 创建 `src/tools/` 目录，将 `function_loader.py` 和 `functions.py` 移入其中，并添加 `__init__.py`
- [ ] 创建 `src/utils/` 目录，将 `api/client.py` 的调用逻辑重构为 `utils/api.py` 中的工具函数，并添加 `__init__.py`
- [ ] 日志文件前缀改为 `v2_chat_`
- [ ] 修改 `load_config`，读取 `config/agents_v2.json`
- [ ] 实现 Agent 创建逻辑：
  - 遍历配置中所有 Agent
  - 将其他参与者名称注入 system prompt 的 `{participants}` 占位符
- [ ] 创建 `ChatRoom` 并添加初始话题
- [ ] 创建 `Scheduler` 并调用 `run()`
- [ ] 调用 `scheduler.run()` 运行对话
- [ ] 程序正常退出

**验收标准**:
- 程序能正确加载 V2 配置
- 成功创建 3 个 Agent 实例
- 每个 Agent 的 system prompt 中包含其他参与者名称（占位符已替换）
- 对话完整运行并输出聊天记录
- 日志文件正确写入 `logs/` 目录

---

### 任务 4: 集成测试

**描述**: 端到端运行 V2，验证多 Agent 对话效果

**依赖**: 任务 1、任务 2、任务 3

**子任务**:
- [ ] 运行 `python src/main.py`，确认无报错
- [ ] 验证控制台输出：alice、bob、charlie 按顺序轮流发言
- [ ] 验证每个 Agent 表现出配置的性格特征
- [ ] 验证日志文件生成在 `logs/` 目录

**验收标准**:
- 3 个 Agent 按轮次循环发言，共 6 轮
- 每个 Agent 发言符合其性格设定
- 程序正常退出（退出码 0）
- 日志文件包含完整聊天记录

---

## 任务依赖关系图

```
任务 1 (配置文件 + Charlie prompt)
    │
    └─ 任务 3 (main.py 修改)
                │
任务 2 (Scheduler) ──┘
                │
                └─ 任务 4 (集成测试)
```

## 开发顺序建议

**推荐顺序**: 任务 1 → 任务 2 → 任务 3 → 任务 4

**并行开发机会**:
- 任务 1 和任务 2 没有依赖关系，可以并行开发

---

## 复用说明

以下 V1 文件**无需修改**，直接复用：

| 文件 | 说明 |
|------|------|
| `src/core/agent.py` | Agent 类，已支持任意消息上下文 |
| `src/core/chat_room.py` | ChatRoom 类，已实现完整功能 |
| `src/tools/function_loader.py` | 工具加载器，从根目录移入 `tools/`，无需改动代码 |
| `src/tools/functions.py` | 工具函数，从根目录移入 `tools/`，无需改动代码 |
| `resource/prompts/alice_system.md` | 直接复用 |
| `resource/prompts/bob_system.md` | 直接复用 |

以下文件需要新建或修改：

| 文件 | 说明 |
|------|------|
| `src/utils/api.py` | 新建，将 `api/client.py` 重构为无状态工具函数 |
| `src/main.py` | 切换配置文件路径，引入 Scheduler，去掉 APIClient 实例化 |

---

## 测试检查清单

- [ ] `agents_v2.json` 格式正确，包含 3 个 Agent
- [ ] `charlie_system.md` 内容完整
- [ ] `Scheduler` 能用 2 个 Agent 正常运行（兼容性）
- [ ] `Scheduler` 能用 3 个 Agent 正常运行
- [ ] 参与者名称正确注入各 Agent 的 system prompt
- [ ] 日志文件能正确生成
- [ ] Alice 表现出热情性格
- [ ] Bob 表现出内敛工程师性格
- [ ] Charlie 表现出哲学教授性格
- [ ] 程序能正常退出

---

## 验收标准（最终）

- [ ] 支持从配置动态加载 3 个及以上 Agent
- [ ] 所有 Agent 在单个聊天室中轮流对话
- [ ] 每个 Agent 表现出配置的性格特征
- [ ] Agent 能感知聊天室内的其他参与者
- [ ] 程序能正常退出
