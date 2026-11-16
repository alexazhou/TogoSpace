# mcporter 风格 console CLI 与 Agent session 开发任务

> 状态：CLI 方案已确认，待实施；本次仅更新文档。  
> 产品文档：[step1_product.md](./step1_product.md)  
> 技术文档：[step2_technical.md](./step2_technical.md)

## 1. 任务表

| 编号 | 任务 | 完成判据 |
|---|---|---|
| D0 | 补齐产品契约 | 完整 CLI 选项、参数与错误行为，以及配置变化对活动 session 的影响写入产品文档 |
| D1 | 补齐技术方案 | 按产品契约明确模块接口、连接标识、并发、取消和生命周期接入点，不以待补充清单代替完成的技术设计 |
| T0 | 清理前序 daemon 试验补丁 | 移除试验 monkeypatch / runtime hook，保留其他工作区改动 |
| T1 | 对齐 mcporter 常规 CLI | list / call / config / resource、server.tool、--args、具名参数和常用显示选项对齐参考语法，实际支持范围明确 |
| T2 | 对接现有服务配置 | config 命令、设置页和 HTTP 接口使用同一份全局注册数据，不新增 bake 配置体系 |
| T3 | 无名称 Agent session | session start / status / stop 不接受名称或 server 参数；每个 Agent 至多一个活动 session，重复 start / stop 幂等 |
| T4 | 跨 server keepalive | start 不连接 server；随后 list / call / resource 按需建立并保留多个 server 连接；重复访问复用，stop 统一关闭 |
| T5 | Agent 隔离与资源清理 | 归属来自可信身份，连接不跨 Agent 共享；Agent / Team / 后端按归属清理，turn 结束和正常休眠保留 |
| T6 | 同步说明与回归 | help、preset、示例和用例统一；移除旧调用包装及具名会话语法；相关验证及真实桌面 smoke 通过 |

产品契约在产品文档维护，技术约束及实现方式在技术文档维护，本表维护工作步骤和验证记录。实施顺序为先补齐 D0 / D1，再清理试验补丁并执行 T1–T6；不把 mcporter 全部功能自动纳入本次任务。

### 1.1 前序试验补丁清理

先前写入的 daemon 试验补丁未提交、未验证，不代表新方案已实现。T0 执行时移除 `src/util/mcp2cliPatch.py`、`scripts/rthook_mcp2cli_session.py` 及相关 runtime hook 引用，保留其他工作区改动。本次只 review 和调整文档，没有撤销该试验代码。

## 2. 验收清单

### 2.1 CLI 语法

- [ ] `mcp list`、`mcp list <server-name>` 和 `mcp list <server-name>.<tool-name> --schema` 按参考语法完成发现。
- [ ] `mcp call <server-name>.<tool-name> --args '<args-json>'` 和具名参数完成调用，参数名称、类型及错误行为按工具 schema 处理。
- [ ] config list / get / add / remove 与现有注册表对接，原有数据保留。
- [ ] resource 使用相同的服务选择和 session 规则。
- [ ] 常规命令的实际支持选项对照 mcporter，新增的 session 命令明确标注为扩展，help 与实际行为一致。
- [ ] 所有示例使用有解释的占位符，不混用旧包装、bake 或具名 session 语法。

### 2.2 无名称 session 与多 server

- [ ] start 不需要名称或 server；未访问服务时 status 显示 session 已开启且没有连接。
- [ ] 未开启时 status 正常返回未开启状态；未开启时 stop 成功。
- [ ] 重复 start 保持原 session 及已有连接，不重置；重复 stop 成功。
- [ ] 同一 session 访问多个 server，分别按需建立连接；再次访问同一 server 复用连接。
- [ ] list / call / resource 和临时来源访问都参与 keepalive；config 操作不因保存定义建立连接。
- [ ] session 开启前后调用语法相同，无需会话选择参数，也无需修改 server 生命周期配置。
- [ ] stop 关闭全部所属连接；之后的操作恢复临时连接并在操作完成后关闭。

### 2.3 隔离与生命周期

- [ ] 同 Team / 不同 Team 的 Agent 访问同一 server，连接独立且不共享 session 状态。
- [ ] status 只返回当前 Agent 的 session；A stop 后 B 的连接和调用继续正常。
- [ ] turn 结束、跨房间、正常休眠及闲置不自动关闭活动 session。
- [ ] Agent 卸载 / 重载、Team 停止 / 重载和后端退出按归属清理，初始化中的连接也结束。
- [ ] 后端重启后 session 未开启，不恢复先前运行中的连接。
- [ ] stdio server 子进程随所属连接退出，初始化失败及关闭过程无残留资源。
- [ ] 调用失败、超时和停止的资源行为在实施阶段明确并验证，不自动重放结果未知的业务调用。
- [ ] console 返回真实成功或失败状态，输出限制和脱敏生效，不读取后端 stdin，不让上游退出行为结束后端。

## 3. 验证与完成记录

按 [测试执行架构](../../tech/07_test/execution_architecture.md) 分层：语法与参数处理放 unit，多 server 协议闭环放 integration，启停和资源清理放 lifecycle；涉及后端接口时增加 api。真实桌面产物验证连接建立、复用和清理。文档修改本身不执行功能测试。

- [x] 确认 mcporter 风格常规 CLI、无名称 session、跨 server keepalive 和 Agent 隔离。
- [x] 按设计目录规范整理产品文档、技术文档和任务表，同步索引与引用。
- [x] Review 文档归属，移除技术文档中重复的产品规则，将试验清理步骤归入任务表。
- [ ] 补齐产品选项与行为契约。
- [ ] 明确具体技术方案。
- [ ] 完成代码迁移。
- [ ] 完成回归与真实桌面验证。
- [ ] 同步最终 help、示例和支持范围。
