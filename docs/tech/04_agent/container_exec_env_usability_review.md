# 容器执行环境使用评审与改进建议（实测报告）

> 来源：2026-11-10 vibe-kanban 容器化部署实战 + 源码调研 + 3 组对照实验（小马哥）
> 状态：方案已重设计（v3，见第 7 章），待立项开发。本文档沉淀实测结论与改进建议，供后续修改时参考。
> 性质说明：实验结果为实测事实（有命令与输出可复现）；人日估算为【推测】。
> v2（2026-11-10 19:45）：新增第 7 章——exit 策略运行时切换方案（gtsp 内部命令方案，Operator 参与讨论后收敛的最终设计）。
> v3（2026-11-16）：第 7 章重设计——改为「Python 侧挂起复用」方案（exit 不 terminate gtsp，下次 enter 切回同一 client），零 gtsp 改动。

---

## 1. 背景

Operator 要求把 vibe-kanban（BloopAI 开源项目）在容器中跑起来（不污染宿主机）。部署过程中遇到两个现象，逐一排查后定位到容器执行环境（`containerService` + `TspAgentDriver`）的若干设计细节，遂做系统梳理：

1. `exit_to_host` 后，容器内后台启动的服务进程死亡，容器却没停；
2. `exit_to_host` 的返回消息声称"容器已自动停止"，与事实不符。

## 2. 架构现状（源码依据）

### 2.1 两种容器形态

| | managed 容器（create_container） | 外部容器（docker run / compose） |
|---|---|---|
| 判定依据 | DB 有 `agent_id + container_id` 记录（`src/service/containerService/core.py:618,655`，`is_managed=record is not None`） | DB 无记录 |
| 接管方式 | `docker start -a -i`——**gtsp 就是容器 PID 1**（`tspDriver.py:215-217`） | `docker exec -i`——gtsp 是额外进程，PID 1 独立 |
| enter 前置 | 创建时已注入 gtsp | 先注入 gtsp（`prepare_container_gtsp`，core.py:692-700） |
| exit 语义 | gtsp 退出 → 容器停回 exited（文件保留，沙箱用完即还） | 只关 gtsp 会话，容器与主进程不受影响 |
| enter 校验 | 只收 created/exited 状态 | 只收 running 状态（`validate_enter_state`，core.py:1096+） |

### 2.2 exit_to_host 流程（`tspDriver.py:187-210`）

```
disconnect()：关闭容器连接的 stdin → 容器内 gtsp 进程退出 →
  managed 容器：PID 1 退出 → Docker 停容器 → 内核 SIGKILL 剩余进程
  外部容器：仅 gtsp 子进程树被清理（前台进程组 SIGHUP 语义）
connect(HOST)：重连宿主
```

## 3. 实测结论（对照实验）

实验环境：外部容器 `node:20`（docker run，PID 1 = node）；容器内分别以下列四种方式启动 `sleep` 类进程，然后 `exit_to_host`，检查存活：

| 方式 | exit_to_host 后 | 机制 |
|---|---|---|
| A. execute_bash 同步命令 + `nohup cmd > log 2>&1 & disown` | **✓ 存活** | nohup 忽略 SIGHUP + `&` 进后台进程组 + 重定向切断 gtsp stdio |
| B. execute_bash `run_in_background` + 命令内 nohup 三件套 | **✓ 存活** | 同上 |
| C. `docker exec -d <容器> cmd` | **✓ 存活** | 独立 session，与 Agent 执行环境完全解耦 |
| D. execute_bash `run_in_background`（裸） | **✗ 死亡** | 进程在 gtsp 前台进程组，gtsp 退出时被 SIGHUP 连带清理 |

补充对照：`docker run node:20 sleep infinity`（PID 1 = sleep，拓扑等价外部容器）中 nohup 进程同样存活 ✓——**存活与否不取决于容器"谁创建"，而取决于 gtsp 是否为 PID 1**。

### 3.1 managed 容器中的结论（推断+源码佐证）

managed 容器 gtsp = PID 1：exit 后容器整体停止，内核对容器内所有进程发 SIGKILL——**不可捕获、不可忽略，nohup 与 docker exec -d 均无效**。managed 容器只适合会话内临时任务（编译/测试/脚本），常驻服务必须放外部容器。

> 注：此条为源码语义推断 + 外部容器对照实验佐证，未直接在 create_container 沙箱上实测（避免污染配额记录）。

### 3.2 nohup 的必要条件

必须 `nohup cmd > log 2>&1 & disown` 三件套齐全：
- 缺 `> log 2>&1`：进程继承 gtsp 的 stdout，管道断裂后写入触发 broken pipe 二次死亡；
- 缺 `&`：留在前台进程组，随 gtsp 一起被清理；
- 缺 nohup：SIGHUP 默认终止进程。

## 4. 定位到的两个具体问题

### 问题 1：exit_to_host 返回文案是静态的（误导）

`src/service/funcToolService/tools.py:1590-1594`：返回 message 写死"你自己创建的容器已自动停止，文件仍保留在容器与挂载目录中"，不区分容器类型。对外部容器（is_managed=False）实际不会停止——本次部署中 Agent 被文案误导，多做了两次 docker restart。

对照：`enter_container`（tools.py:1561-1567）的消息是按 `target.is_managed` 动态生成的（外部容器正确提示"退出后它仍会继续运行"）。两处不一致。

**修复建议**：exit_to_host 返回时按当前容器的 is_managed 动态生成文案（需在 disconnect 前记住 target）。一行级修复。

### 问题 2：后台进程死亡是静默的

`execute_bash(run_in_background)` 启动时返回 success + process_id，`exit_to_host` 后进程被清理，无任何提示；失败要到"端口连不上"等远端症状才暴露。本次部署在这一点上反复消耗了数轮排查。

**改进建议**（三选一或组合，按成本递增）：
1. **文档层（半天）**：execute_bash 工具描述写明"后台进程绑定当前执行环境会话，exit_to_host 后会被终止"；配套 doc/tech 文档沉淀四种姿势与存活矩阵；
2. **退出提示（1 人日【推测】）**：exit_to_host 时检测当前会话存活的后台进程，返回消息列出"以下后台进程将被终止：..."；
3. **行为改造（2-3 人日【推测】）**：容器内 run_in_background 自动 setsid 脱离进程组（效果≈nohup）——最彻底，但需评估孤儿进程泄漏与 managed 容器语义冲突（managed 下容器即停，脱离也救不了）。

## 5. 其他改进观察（优先级排序）

| # | 建议 | 成本 | 说明 |
|---|---|---|---|
| 3 | create_container 无端口映射、无引导 | 半天（文档） | 跑 Web 服务必然撞墙。工具描述应写明限制与替代方案（docker run + enter_container）；或加 ports 参数（涉及安全策略，需评估） |
| 4 | 存活规则文档化 | 半天 | "外部不停 / managed 全停 / nohup 三件套 / docker exec -d"矩阵目前只存在于源码注释，用户/Agent 无从得知 |
| 5 | 单执行环境模型 | 远期 | 一次只能"在"一个容器，多容器协作（A 编译 + B 跑服务）需要反复 enter/exit。可考虑多环境句柄 |

> 注：改进项 3（create_container 端口映射）与本文 §7 的 exit 策略方案相互独立；若未来 create_container 增加 ports 参数，建议与 `stop_on_exit` 能力组合评估（managed 常驻服务容器场景 = 端口映射 + keep_alive 保活）。

## 6. 实践速查（沉淀给 Agent/用户）

```
容器内起常驻服务（要端口映射的）：
  ① docker run -d --name xxx -p 127.0.0.1:<port>:<port> <image> sleep infinity   # 手动创建，控制端口
  ② docker exec -d xxx sh -c "HOST=0.0.0.0 PORT=<port> <启动命令> >> /data/log 2>&1"  # 独立 session 启动
  ③ enter_container <12位ID> 进入做临时操作；exit_to_host 不影响服务

注意：
  - 服务必须绑定 0.0.0.0（默认 127.0.0.1 只在容器回环，端口映射不通）
  - 显式指定 PORT（很多服务默认随机端口）
  - 数据持久化用 docker volume
  - execute_bash 后台进程（不 nohup）活不过 exit_to_host
  - managed（create_container）容器退出即停机，不适合常驻服务
```

## 7. 方案设计：exit 保活 + gtsp 挂起复用（v3，2026-11-16，Operator 定向重设计）

### 7.0 方案演进

原 v2 方案（gtsp 加 `set_exit_policy` 内部命令 + EOF 挂住）依赖改 Go 侧，本次被否决，理由：改动跨语言、引入新协议，且「保活」之后再次 enter 仍要走 `docker start/exec` 重新拉起（保活 ≠ 复用）。Operator 重定向为**纯 Python 侧**方案：exit 时不 terminate gtsp、把它挂起，下次 enter 同一容器直接切回同一个 client。

核心洞察（源码依据）：现状 `exit_to_host` 里 `disconnect()` 的底层是 `TSPClient.disconnect()` → `process.terminate()`（pytspclient/client.py:99-124），**不是**「关 stdin 等 EOF」。也就是说只要不调用 terminate、保持子进程对象与 stdin 管道打开，gtsp 就继续读 stdin、不 EOF、不退出；managed 容器里 gtsp 是 PID 1，gtsp 活着 = 容器 running。破局点从原方案的「让 gtsp 在 EOF 时挂住」退化成「根本不让 EOF 发生」——零 gtsp 改动。

### 7.1 三个已拍板决策（Operator）

| # | 决策 | 内容 |
|---|---|---|
| D1 | 复用范围 | 多容器并存：挂起池 `dict[container_id → client]`，每个保留的容器一个挂起 gtsp |
| D2 | 默认值 | `keep_alive: bool = False`——不传保持现状（容器停止），显式传 True 才保活 |
| D3 | 容器类型 | 全部容器：managed 保活 + 外部容器也复用（省每次重新 exec + initialize） |

### 7.2 核心机制：挂起池

```
self._suspended_clients: dict[str, TSPClient]   # container_id → 挂起的 gtsp client
```

- **挂起**：exit(keep_alive=True) 时把 `self._client` 移入池，**不调 disconnect / 不 terminate**。client 的 `_read_task` / `_stderr_task` 继续后台跑（读 stdout/stderr），stdin 管道保持打开 → gtsp 常驻。
- **复用**：enter 同一容器时从池里取出赋回 `self._client`，`_load_tsp_tools` 重载工具，跳过 `docker start/exec` + `initialize` 握手。
- **健康检测**：复用前检查 `process.returncode is None`（`_is_client_connected` 同款口径）；死了就丢弃、走正常拉起。
- **单执行环境不变**：`self._client` 仍是单数（当前活跃），池是「暂停」不是「并发」。

### 7.3 具体改动（全在 Python 侧）

| 文件 | 改动 |
|---|---|
| `tools.py` `exit_to_host` | 加 `keep_alive: bool = False`；返回文案按 `keep_alive` + `is_managed` 动态生成（顺手修 §4 问题1 静态文案） |
| `tspDriver.exit_to_host` | 加 `keep_alive` 形参：True 时挂起当前容器 client 再 `connect(HOST)`；False 走现状 `disconnect` + `connect` |
| `tspDriver.enter_container` | `resolve` 之后、`validate` 之前查挂起池：命中活 client 直接复用短路（绕过状态校验与 docker 拉起） |
| `tspDriver` 挂起池字段 | 新增 `_suspended_clients` + 复用/清理辅助 |
| `tspDriver.shutdown` | 除 `self._client` 外，terminate 池内全部 client |
| `delete_container` 联动 | 挂起 gtsp 随容器 `rm -f` 自然死亡，复用前 returncode 兜底；可选在 driver 侧加主动清池 |

### 7.4 enter 复用为何不需要改 validate_enter_state

复用路径在 `validate_enter_state` **之前**短路，因此状态校验原样保留：

- 有活挂起 gtsp → 直接切回，不碰状态校验（此时 managed 容器 running 是「保活中」的合法态）；
- 无挂起 → 走原校验。managed 容器 running 仍判「异常残留」（保活的 gtsp 死了、容器却 running，本就是该排查的异常）。

这样 `validate_enter_state` 与 `_build_container_command` 都零改动，改动面进一步收窄。

### 7.5 生命周期矩阵（改后）

| 操作 | 结果 |
|---|---|
| enter（无挂起，自建 created/exited） | `docker start -a -i`（现状） |
| enter（无挂起，外部 running） | `docker exec -i`（现状） |
| exit（默认 keep_alive=False） | terminate gtsp → 自建容器停 / 外部 gtsp 退出（现状） |
| exit（keep_alive=True） | gtsp 挂起，容器保持 running |
| 再次 enter 同一容器 | 直接切回挂起 gtsp，跳过拉起 + initialize |
| enter 时挂起 gtsp 已死 | 丢弃池内残留，走正常拉起（自建 running → 异常残留报错） |
| delete_container / docker stop | 容器内 gtsp 随之死，池内残留靠复用前检测清理 |

**附带收益**：keep_alive 挂起期间 execute_bash 的后台进程不会被清理（gtsp 未退出即不触发清理链），§4 问题2 的 run_in_background 静默死亡在此形态下自动消解。

### 7.6 与原 set_exit_policy 方案对比

| 维度 | 原方案（gtsp set_exit_policy） | 新方案（Python 侧挂起复用） |
|---|---|---|
| gtsp 改动 | 加内部命令 + EOF 分支（~20 行 Go）+ 重编译三平台二进制 | **零** |
| 协议扩展 | 新增请求类型 | **零** |
| 再 enter | 仍需 docker start/exec + initialize | 直接切回，省握手 |
| 多容器并存 | EOF 挂住是全局态，未定义多容器语义 | 挂起池天然支持 |
| 进程常驻 | 挂住的 gtsp 常驻 | 挂起的 gtsp 常驻（相同） |
| 风险 | 跨语言协议面 | 纯 Python，无跨语言面 |

### 7.7 已知限制与待定

1. **资源常驻**：每保留容器一个 gtsp 进程（数 MB 内存）+ managed 容器 running 占用的容器资源，多容器并存时线性增长，无自动回收（TTL 不在本期）。
2. **单执行环境**：仍是「一个活跃 + N 个挂起」，不提供多容器并发执行。
3. **挂起 client 的 read task 生命周期**：gtsp 进程退出后其 stdout EOF，`_read_task` 自然结束；client 对象由池持有，直到复用前检测清理。
4. **工具列表一致性**：复用 client 的 tools 未变（gtsp 进程未重启），`_load_tsp_tools` 重载保证与 driver 状态一致。

### 7.8 工作量估算【推测】

| 项 | 估算 |
|---|---|
| tspDriver：挂起池 + exit/enter 逻辑 + shutdown | 0.5 人日 |
| tools.py：keep_alive 参数 + 文案动态化 | 0.5 人日 |
| 单测：默认/保活/复用/健康检测/外部容器/清理 | 1 人日 |
| 回归（测试只用本地 sqlite） | 0.5 人日 |
| **合计** | **约 2–2.5 人日** |

---

## 8. 本次实测现场记录

- 外部容器：vibe-kanban-app（node:20，127.0.0.1:3000→3000，docker exec -d 启动 vibe-kanban v0.1.44，服务验证 HTTP 200）
- 恢复命令：`docker start vibe-kanban-app && docker exec -d vibe-kanban-app /data/start.sh`
- 实验容器（vk-managed-test 等）均已清理
