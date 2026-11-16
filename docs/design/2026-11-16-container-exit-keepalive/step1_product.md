# 容器执行环境 exit_to_host 保活与挂起复用 — 产品文档

> 日期：2026-11-16  
> 状态：方案已确认，待立项开发  
> 来源：2026-11-10 容器化部署实战与容器执行环境可用性评审  
> 技术文档：[step2_technical.md](./step2_technical.md)

本文维护产品目标、核心决策、功能行为、生命周期矩阵、工具文案规范与验收标准。技术实现细节与模块改动见技术文档。

---

## 1. 背景与痛点

在容器化部署实战（vibe-kanban 容器化部署）与源码排查中，当前容器执行环境（`create_container` / `enter_container` / `exit_to_host`）暴露出以下可用性问题：

1. **`exit_to_host` 返回文案静态误导**  
   无论当前处于自建容器（`is_managed=True`）还是外部容器（`is_managed=False`），`exit_to_host` 返回消息均写死“你自己创建的容器已自动停止，文件仍保留在容器与挂载目录中”。对外部容器而言，退出后容器实际仍在运行，Agent 被误导后会做出多余的 `docker restart` 等操作。
2. **退出容器无法保留运行态与会话**  
   现状 `exit_to_host` 会直接终止容器内 `gtsp` 进程：
   - 自建容器中 `gtsp` 是 PID 1，退出即导致容器停回 `exited`，容器内所有后台进程被内核 `SIGKILL` 强制清理；
   - 外部容器中 `gtsp` 退出会连带清理其前台进程组（`execute_bash(run_in_background)` 启动的裸后台进程静默死亡），且下次 `enter_container` 必须重新 `docker exec` + `initialize` 握手。
   - 当 Agent 只是临时回宿主机查看文件或调用宿主工具、稍后还要回同一容器继续工作时，缺少轻量“暂停/切回”能力。
3. **后台进程静默死亡与常驻服务限制缺乏引导**  
   `execute_bash(run_in_background)` 在 `exit_to_host`（非保活模式）时会被静默清理，直到后续访问端口失败才暴露；`create_container` 不支持端口映射，跑常驻 Web 服务时容易踩坑，但工具描述与文档缺少明确指引。

---

## 2. 目标与产品决策

### 2.1 核心目标

- 为 `exit_to_host` 增加可选的**保活挂起**能力（`keep_alive`），让 Agent 在退回宿主机时可以按需保留容器及其会话进程，并在下次 `enter_container` 时直接复用。
- 修复 `exit_to_host` 的静态文案误导，按**保活状态 × 容器类型**动态返回准确提示。
- 补全容器工具描述与实践文档，明确后台进程存活规则与常驻 Web 服务的推荐用法。

### 2.2 已拍板决策

| 编号 | 决策项 | 结论 | 说明 |
|---|---|---|---|
| **D1** | 复用范围 | **多容器并存（挂起池）** | Agent 可将多个容器各自挂起保留（每个保留容器对应一个挂起的 `gtsp` 会话），按 `container_id` 切回复用；仍保持“同一时刻最多一个活跃执行环境”的模型 |
| **D2** | 默认行为 | **`keep_alive: bool = False`** | 不传或传 `False` 保持现有行为（自建容器停止、外部容器仅退出本次 `gtsp` 会话）；仅在显式传 `True` 时保活挂起 |
| **D3** | 容器类型 | **全部容器适用** | 自建容器（`is_managed=True`）：保活期间容器保持 `running`，后台进程不中断；外部容器（`is_managed=False`）：同样挂起复用，省去下次 `enter_container` 重新 `docker exec` + `initialize` 握手，且会话内后台进程不被清理 |

### 2.3 本期范围与非目标

- **本期包含**：
  - `exit_to_host` 新增 `keep_alive: bool = False` 参数及动态返回文案；
  - `enter_container` 支持命中挂起会话时直接切回复用；
  - 容器工具（`exit_to_host`、`create_container` 等）描述完善与使用规范沉淀。
- **本期不包含（非目标）**：
  - 挂起会话的 TTL 自动超时回收；
  - 多容器并发执行（不改变单活跃执行环境模型）；
  - `create_container` 新增端口映射（`ports`）参数；
  - `execute_bash` 容器内 `run_in_background` 自动 `setsid` 脱离进程组改造。

---

## 3. 功能行为与规范

### 3.1 `exit_to_host` 参数与行为

工具签名新增可选参数 `keep_alive: bool = False`：

- **`keep_alive=False`（默认，向后兼容）**：
  - 断开并终止当前容器的 `gtsp` 会话，切回宿主机执行环境。
  - 自建容器因 PID 1 退出而停回 `exited`（文件保留在容器与挂载目录）；外部容器继续保持运行，仅本次 `gtsp` 会话及未脱离会话的后台进程结束。
- **`keep_alive=True`（保活挂起）**：
  - 不终止当前容器的 `gtsp` 进程，将其会话挂起保存在该 Agent 的挂起池中，执行环境切回宿主机。
  - 自建容器保持 `running` 状态，容器内通过 `execute_bash(run_in_background)` 启动的后台进程继续存活；外部容器的 `gtsp` 会话同样保持存活，等待后续复用。
- **已在宿主机时调用（幂等）**：
  - 无论 `keep_alive` 取何值，均直接返回成功，不重复重连宿主机。

### 3.2 `exit_to_host` 动态返回文案

`exit_to_host` 的返回 `message` 必须结合**退出前的容器类型（`is_managed`）**与 **`keep_alive`** 动态生成，避免误导 Agent：

| 退出前环境 | `keep_alive` | 容器与会话实际结果 | 返回文案要点 |
|---|---|---|---|
| 自建容器（`is_managed=True`） | `False` | 容器已停止（`exited`），文件保留 | 提示已回宿主、容器路径不可用；说明“你自己创建的容器已自动停止，文件仍保留在容器与挂载目录中” |
| 外部容器（`is_managed=False`） | `False` | 本次会话退出，容器本身仍在运行 | 提示已回宿主、容器路径不可用；说明“该容器不是由你创建的，退出后它仍会继续运行（本次会话内的未脱离后台进程已随会话结束）” |
| 自建容器（`is_managed=True`） | `True` | 容器保持 `running`，会话已挂起 | 提示已回宿主、容器路径暂不可用；说明“容器已保持运行并挂起当前会话，后台进程继续存活；再次调用 `enter_container` 可直接恢复” |
| 外部容器（`is_managed=False`） | `True` | 容器与会话均保持运行并挂起 | 提示已回宿主、容器路径暂不可用；说明“已挂起该外部容器的会话，容器与会话内后台进程继续存活；再次调用 `enter_container` 可直接恢复” |
| 已在宿主机 | `False` / `True` | 无容器退出动作 | 提示“当前已处于宿主机环境，宿主工作目录生效” |

### 3.3 `enter_container` 挂起复用行为

当 Agent 再次调用 `enter_container(container_id)` 进入一个此前通过 `keep_alive=True` 挂起的容器时：

1. **命中存活的挂起会话**：直接切回该挂起会话，无需重新执行 `docker start` / `docker exec` 及握手初始化，容器内此前保活的进程与状态无缝延续。
2. **挂起会话已失效（例如容器在宿主机被外部 `docker stop` / `docker rm` 停止或删除）**：自动清理失效的挂起记录，回退到正常进入校验与拉起流程（若容器已不存在或状态异常，按原有错误码清晰报错）。
3. **跨容器直接切换**：若当前正处于容器 A 内、直接调用 `enter_container(B)`（未先调用 `exit_to_host`），则容器 A 按正常断开（非保活）处理，容器 B 若有存活挂起会话则直接复用，否则走正常拉起流程。

### 3.4 进程存活与容器生命周期矩阵

供工具描述与文档明确传达给 Agent 与用户：

| 操作 / 启动方式 | 自建容器（`keep_alive=False`） | 自建容器（`keep_alive=True`） | 外部容器（`keep_alive=False`） | 外部容器（`keep_alive=True`） |
|---|---|---|---|---|
| `execute_bash(run_in_background)` 裸启动 | ✗ 容器停机，内核 `SIGKILL` 清理 | ✓ 容器 `running`，进程存活 | ✗ 随 `gtsp` 退出被 `SIGHUP` 清理 | ✓ `gtsp` 挂起，进程存活 |
| `nohup cmd > log 2>&1 & disown` 三件套 | ✗ 容器停机，内核 `SIGKILL` 清理 | ✓ 容器 `running`，进程存活 | ✓ 脱离进程组，持续存活 | ✓ 持续存活 |
| `docker exec -d <容器> cmd`（宿主机执行） | ✗ 容器停机，内核 `SIGKILL` 清理 | ✓ 容器 `running`，进程存活 | ✓ 独立 session，持续存活 | ✓ 持续存活 |
| 再次 `enter_container` | `docker start -a -i` 重新拉起 | 直接切回挂起会话 | `docker exec -i` 重新拉起 | 直接切回挂起会话 |

### 3.5 工具描述与使用指引补充

在工具 docstring（模型可见的工具说明）中补充关键约束，减少试错成本：

- **`exit_to_host`**：说明 `keep_alive` 参数语义，以及非保活退出时自建容器停机、未脱离会话的后台进程会被终止的事实。
- **`create_container`**：明确其定位为“隔离沙箱，无端口映射”；如需运行带端口映射的常驻 Web 服务，应引导使用宿主机 `docker run -d -p 127.0.0.1:<port>:<port>` 创建外部容器，再配合 `docker exec -d` 或 `enter_container(..., keep_alive=True)` 使用，并提醒服务需绑定 `0.0.0.0` 与显式端口。

---

## 4. 典型交互示例

### 场景 1：自建容器内保活后台服务，回宿主机操作后再秒级切回

1. Agent 创建并进入自建容器 `a1b2c3d4e5f6`，在容器内通过 `execute_bash(run_in_background=True)` 启动构建监听或测试服务。
2. Agent 需要回宿主机读取宿主机配置或向房间同步结果，调用：
   `exit_to_host(keep_alive=True)`
   → 返回 `exec_env: "host"`，提示自建容器保持运行、会话已挂起、后台进程继续存活。
3. Agent 在宿主机完成操作后，调用：
   `enter_container("a1b2c3d4e5f6")`
   → 直接复用挂起会话切回容器，无需等待容器重启与握手，容器内后台服务仍在运行。
4. 全部任务完成后，Agent 调用：
   `exit_to_host()`（默认 `keep_alive=False`）
   → 终止 `gtsp`，自建容器自动停回 `exited`。

### 场景 2：外部容器普通退出，文案不再误导

1. Agent 进入宿主机上已在运行的外部容器 `f6e5d4c3b2a1`。
2. 完成操作后调用 `exit_to_host()`（默认 `keep_alive=False`）。
3. 返回文案明确告知“该容器不是由你创建的，退出后它仍会继续运行”，Agent 不会误以为容器已停止而执行多余的 `docker restart`。

---

## 5. 验收标准

1. **默认行为兼容**：不传 `keep_alive` 或传 `keep_alive=False` 时，自建容器退出后停回 `exited`，外部容器退出仅关闭本次 `gtsp` 会话、容器继续运行，与既有行为一致。
2. **保活与复用有效**：
   - `exit_to_host(keep_alive=True)` 后，自建容器保持 `running`，容器内 `run_in_background` 进程持续存活；
   - 外部容器 `exit_to_host(keep_alive=True)` 后会话挂起、进程存活；
   - 多个容器可先后以 `keep_alive=True` 挂起并存，按 `container_id` 分别切回复用且互不干扰。
3. **动态文案准确**：4 种组合（`is_managed` × `keep_alive`）及“已在宿主”幂等场景的返回 `message` 均与实际容器/会话状态一致。
4. **异常与清理兜底**：
   - 挂起期间容器被外部停止或删除时，再次 `enter_container` 能自动清理死会话并走正常校验/报错流程；
   - `delete_container` 可正常删除处于挂起状态（非当前活跃）的自建容器；
   - Agent 关闭（`shutdown`）或系统清理（如 `switch_agent_to_host`）时，所有挂起会话均能正确释放，无孤儿进程泄漏。
