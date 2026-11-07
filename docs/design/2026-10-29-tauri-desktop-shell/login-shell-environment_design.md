# 桌面端登录 shell PATH 读取：DSH 实现分析与 TogoSpace 移植方案

> 状态：调研完成，待实施
> 日期：2026-11-08
> 所属里程碑：V28 Tauri 桌面外壳
> DSH 参考版本：`deepseek-ai/deepseek-harness` master `fcc0ad4621`（2026-11-04）；登录 shell 功能合并于 2026-09-29（PR #5393）

## 1. 问题

macOS / Linux 从 Dock、Finder 或应用菜单启动的图形程序，只继承会话管理器（launchd / systemd user session）提供的环境，不会执行用户的 shell 启动文件。因此 `~/.zprofile`、`~/.zshrc` 里导出的 `PATH` 在桌面应用里不存在。

TogoSpace 的表现：

- Tauri 外壳进程拿到的是最小 `PATH`（通常只有 `/usr/bin:/bin:/usr/sbin:/sbin`）；
- Rust 通过 `Command` 拉起 Python sidecar，sidecar 继承同一份最小 `PATH`；
- Python 后端里的 `shutil.which("docker")`、Agent 工具调用、`asyncio.create_subprocess_exec` 等都受影响；
- 已有 `src/util/containerUtil.py` 的 docker 常见路径兜底，但只能覆盖 docker，覆盖不了 git、node、pnpm、brew 安装的其他命令。

从终端直接运行 `backend_main.py` 或 `desktopEntry.py` 时 `PATH` 正常，不需要处理。因此修复点应放在**唯一能区分「GUI 启动」与「终端启动」的 Tauri Rust 外壳**，读取一次登录 shell 的 `PATH` 并注入 sidecar，Python 侧不做改动。

## 2. 结论摘要

- 参考 DSH 的探测机制，但**在 TogoSpace 中做裁剪简化：只读取并注入 `PATH`，不获取其他环境变量**。
- 简化为只取 `PATH` 的收益：
  - 不需要维护复杂的环境变量排除黑名单（天然不会覆盖 `STORAGE_ROOT`、`TOGOSPACE_*` 等启动器变量）；
  - 不需要在进程间搬运完整环境表，也不会触碰用户 shell 中的其他变量；
  - 输出解析从「`env -0` 多键值切分 + 过滤合并」简化为「提取两个定界符之间的 `PATH` 值并做分段清洗与校验」。
- 实现位置放在 Rust 外壳，而不是 Python sidecar：
  - Rust 是 GUI 启动的唯一入口；
  - 读取一次后通过 `command.env("PATH", ...)` 注入 sidecar，后端及其所有子进程自然继承；
  - Python 从终端单独运行时本来就没有这个问题，不需要在业务进程中重复实现。
- 关键工程要点：
  1. 子进程使用 `setsid()` 脱离控制终端并建立独立进程组，避免从终端启动时因 `.zshrc` 访问 `/dev/tty` 触发 `SIGTTIN` / `SIGTTOU` 挂起；
  2. 仅对已知 POSIX shell（`zsh` / `bash` / `sh`）执行探针，并对提取出的 `PATH` 按 `:` 分段过滤非绝对路径条目（保留合法绝对路径），既避免非 POSIX shell 的「假成功」，又不会因单条相对路径（如 `.:$PATH`）误杀整段 `PATH`；
  3. 单条 `printf` 同时输出双端 NUL 定界符与 `$PATH`，读到第二个定界符即完成（不等 stdout EOF），读取线程忽略通道发送错误并 detached 持续排水，主流程带宽限期回收子进程防止僵尸进程；
  4. 收紧超时预算（总预算 5000ms，两段式分配单候选上限），不阻塞 Tauri 主线程，最坏推迟 sidecar 启动不超过总预算；
  5. 开发模式（`debug_assertions` / `tauri dev`）默认跳过探测以保留终端当前会话的临时 `PATH`（可通过 `TOGOSPACE_LOGIN_SHELL_PROBE=1` 强制开启）；Windows 直接跳过。

## 3. DSH 实现分析与简化对照

### 3.1 DSH 代码位置

| 文件 | 作用 |
|------|------|
| `apps/desktop/src/login-shell-environment.ts` | 核心实现，185 行 |
| `apps/desktop/tests/login-shell-environment.spec.ts` | 单元测试，188 行 |
| `apps/desktop/src/main.ts:385-396` | 应用启动时发起读取，`will-quit` 时中止 |
| `apps/desktop/src/main.ts:524-529` | 启动 Host 前传入合并后的环境 |
| `apps/desktop/src/host-process.ts:217-224` | Host 子进程使用该环境 |
| `.agents/notes/archived/feature/2026-09-28-desktop-login-shell-environment.zh.md` | 设计决策记录 |

### 3.2 核心探测机制（复用部分）

1. **确定候选 shell（仅限 POSIX shell）**
   - 第一候选来自用户数据库（POSIX `getpwuid_r` 的 `pw_shell`），不是 `$SHELL`；
   - **POSIX 校验**：仅当账户 shell 的可执行文件 basename 属于已知 POSIX shell（`zsh`、`bash`、`sh`）时才纳入第一候选；若账户 shell 为 `fish`、`tcsh`、`csh`、`nu` 等非 POSIX shell，记录日志后直接跳过，避免非 POSIX 语法下 `$PATH` 展开行为不同造成「假成功」；
   - 其后依次追加 `/bin/zsh`、`/bin/bash`、`/bin/sh`，按完整路径去重；
   - 账户记录不可读时只用系统 shell（注：部分 Linux 发行版 `/bin/sh` 为 `dash`，可能不支持 `-l` 或只读 `~/.profile`，作为兜底快速失败或返回基础路径，属预期行为）。

2. **执行登录 shell**
   - 参数：`<shell> -ilc <dump>`；
   - `-i` 交互模式：确保 `~/.zshrc` 等只在交互式 shell 读取的文件生效；
   - `-l` 登录模式：读取 `~/.zprofile` / `~/.profile`；
   - `cwd` 为用户 home（优先 `HOME`，缺失时取 `getpwuid_r` 的 `pw_dir`）；
   - `stdin` 关闭（`Stdio::null()`），避免 rc 文件里的交互提示等待输入；
   - `stdout` 接管道；`stderr` 丢弃；
   - 独立会话与进程组（`setsid`），便于脱离控制终端并在超时/中止时整组结束；
   - 无 TTY 保护：为探针子进程设置 `DISABLE_AUTO_UPDATE=true`、`ZSH_TMUX_AUTOSTARTED=true`、`ZSH_TMUX_AUTOSTART=false`，避免 oh-my-zsh 更新提示、tmux 自动启动插件在无终端环境下阻塞。

3. **提前完成（不等 stdout EOF）**
   - rc 文件启动的后台进程会继承 stdout 并长期持有，等 EOF 会每次超时；
   - 出现第二个定界符即完成提取；
   - 读取线程以 detached 方式在后台继续排水直到 EOF（主流程不 `join` 该线程），避免后台进程写 stdout 时收到 EPIPE，同时保证主流程不会被长期持有 stdout 的孙进程卡死。

### 3.3 针对 TogoSpace 的简化（仅取 `PATH`）

| 维度 | DSH 原方案（全量环境） | TogoSpace 简化方案（仅 `PATH`） |
|------|------------------------|---------------------------------|
| 探针命令 | `printf '\0<DELIM>\0'; command env -0 \|\| exit; printf '\0<DELIM>\0'; exit` | `printf '\0<DELIM>\0%s\0<DELIM>\0' "$PATH"; exit` |
| 输出解析 | 按 NUL 切分多条 `key=value`，处理值内换行与非法项 | 提取两个 `\0<DELIM>\0` 之间的字节片段，按 `:` 过滤保留绝对路径 |
| 变量过滤 | 维护 `PWD`、`SHLVL`、`DSH_*`、`ELECTRON_*` 等排除规则 | **无需过滤**（只取 `PATH`） |
| 返回类型 | `Vec<(OsString, OsString)>` | `Option<&'static OsStr>` |
| 注入方式 | `command.envs(...)` + 后置覆盖保护 | `login_shell::inject_path(&mut command)` |

## 4. TogoSpace 现状与落点

### 4.1 现状

| 位置 | 现状 |
|------|------|
| `tauri/src-tauri/src/sys/macos.rs:10-35` | `get_command` 构造 sidecar 命令，默认继承 Tauri 进程环境 |
| `tauri/src-tauri/src/backend.rs:166-174` | 设置 `TOGOSPACE_DESKTOP`、`STORAGE_ROOT`、端口、token、父 PID、`PYTHONUNBUFFERED` |
| `tauri/src-tauri/src/lib.rs:169-173` | `backend::start` 在独立线程中执行，阻塞读取不会卡住 Tauri 主线程 |
| `tauri/src-tauri/src/backend.rs:17` | `START_TIMEOUT_MS = 15_000`，在 `command.spawn()` 之后才启动计时 |
| `src/util/containerUtil.py:22-46` | 只有 docker 的常见路径兜底，没有通用 shell `PATH` 读取 |
| `tauri/src-tauri/Cargo.toml` | `libc` 已在 `cfg(unix)` 依赖中，无需新增依赖 |

### 4.2 集成位置与阻塞契约

新增模块 `tauri/src-tauri/src/login_shell.rs`，在 `lib.rs` 中声明 `mod login_shell;`，对外暴露：

```rust
pub fn path() -> Option<&'static OsStr>
pub fn inject_path(command: &mut std::process::Command)
```

- **缓存与阻塞契约**：
  - 内部使用 `OnceLock<Option<OsString>>`（通过 `cached_path(&CACHE, resolve_path)`）保证每个应用进程最多探测一次（成功或失败均缓存到进程结束，失败需重启应用）；
  - `path()` **允许阻塞当前调用线程**：当 `setup()` 中的预热线程尚未完成时，`backend::start`（运行在独立后台线程）调用 `path()` 会同步等待 `OnceLock` 初始化完成（受总预算上限约束，且在 `crate::is_quitting()` 时提前退出）；
  - 预热的作用是让探测与 `splashscreen` WebView 冷启动阶段重叠以扣减感知耗时，但若 `splashscreen` 先于探测完成，剩余探测耗时仍会叠加在 `command.spawn()` 之前；
  - 该阻塞**只发生在 `backend::start` 后台线程，绝不阻塞 Tauri 主线程**。
- **预热与注入点**：
  - 在 `lib.rs::setup` 完成 `logger::init` 后，通过 `std::thread::spawn(|| { let _ = login_shell::path(); });` 触发后台预热；
  - 在 `backend.rs::start` 中构造 `command` 时调用 `login_shell::inject_path(&mut command)`（内部若 `path()` 返回 `Some(p)` 则执行 `command.env("PATH", p)`）；
  - 不调用 `std::env::set_var` 修改 Tauri 外壳全局环境。

## 5. 详细设计

### 5.1 算法与系统调用细节

1. **脱离控制终端与独立进程组（`setsid`）**
   - Node 的 `{ detached: true }` 在 Unix 下底层调用的是 `libc::setsid()`，而非 `CommandExt::process_group(0)`（`setpgid(0, 0)`）。
   - 若仅用 `setpgid(0, 0)`，从终端启动应用时子进程仍挂载在父进程的控制终端（`/dev/tty`）下且处于后台进程组；一旦 `.zshrc` 访问 `/dev/tty`（如 `stty`、`gpg-agent`、Powerlevel10k instant prompt），内核会发送 `SIGTTIN` / `SIGTTOU` 导致子进程挂起直至超时。
   - 因此使用 `CommandExt::pre_exec` 调用 `libc::setsid()`：既脱离控制终端，又使 `PGID == PID`，超时或中止时通过 `libc::kill(-(pid as i32), libc::SIGKILL)` 即可清理该进程组。

2. **定界符与单条 `printf` 命令**
   - 定界符常量：`const DELIMITER: &str = "__TOGOSPACE_LOGIN_SHELL_PATH__";`
   - 定界符**不能以八进制数字 `0`–`7` 开头**，否则 POSIX `printf '\0...'` 会将 `\0` 与后续数字当作八进制转义 `\0ddd` 吞掉字节。
   - 探针命令（必须使用**单条 `printf`** 同时输出首尾定界符与 `$PATH`，避免拆成两次 `printf` 扩大并发输出插入的窗口）：
     ```text
     printf '\0__TOGOSPACE_LOGIN_SHELL_PATH__\0%s\0__TOGOSPACE_LOGIN_SHELL_PATH__\0' "$PATH"; exit
     ```
   - 完整字节标记：`b"\0__TOGOSPACE_LOGIN_SHELL_PATH__\0"`。

3. **流式读取、缓冲区上限与 detached 排水线程**
   - 读取线程循环读取子进程 `stdout`，通过 `mpsc::sync_channel(1)` 向主流程汇报结果：
     - 在缓冲区中查找两次完整字节标记 `\0__TOGOSPACE_LOGIN_SHELL_PATH__\0`（不能只按子串匹配，防止 rc 杂音误触发）；
     - 匹配成功前限制累积缓冲区上限为 `256 KiB`，若超出仍未集齐两个标记，通过 `let _ = tx.send(Err(...));` 通知主流程并中止累积，防止死循环 `.zshrc` 刷屏撑爆内存；
     - 一旦出现第二个标记，立即将两个标记之间的字节切片通过 `let _ = tx.send(Ok(slice));` 发送给主流程；
     - **忽略 `send` 错误继续排水**：若主流程因超时或应用退出已提前 drop `Receiver`，`tx.send(...)` 会返回 `Err`，读取线程**必须忽略该错误（绝不能 `unwrap` panic）**，丢弃累积缓冲后继续用固定小缓冲循环 `read` 排水直至 EOF，防止后台进程写满管道阻塞或收到 `SIGPIPE`。
   - **不 `join` 读取线程**：交互式 shell 中由 rc 启动的后台任务在 job control 或 `setsid` 下可能拥有独立进程组，`kill(-pid, SIGKILL)` 杀不到它们；若它们持有 `stdout`，读取线程永远等不到 EOF。因此主流程**绝不 `join` 读取线程**（任其 detached 运行，最坏在进程生命周期内滞留一个阻塞在 `read` 上的排水线程）。

4. **子进程回收（防止僵尸进程与 EXIT trap 阻塞）**
   - Rust 的 `std::process::Child` 在 `Drop` 时不会自动 `wait()`。
   - 收到第二个定界符后，`<shell>` 紧接着执行 `; exit`，正常在几毫秒内退出：
     - 调用方在短宽限期（`100ms`，不超过剩余预算）内轮询 `child.try_wait()`；
     - 若宽限期内未退出（例如用户的 `zshexit` / `EXIT` trap 阻塞），调用 `kill_group(pid)` 强杀探针进程组并执行 `child.wait()` 回收，确保既不阻塞启动，也不残留 `<defunct>` 僵尸进程。
   - 若超时、spawn 后读取失败或收到应用退出信号（`crate::is_quitting()`），同样调用 `kill_group(pid)` + `child.wait()`。

5. **PATH 提取与分段清洗校验**
   - 截取两个完整标记之间的字节序列后，执行分段清洗与校验（防止单次 `printf` 输出期间 rc 后台进程并发写 stdout 混入杂音，同时兼容用户配置中包含的相对路径条目如 `.:$PATH` 或 `bin:$PATH`）：
     - 原始片段不得为空，且不得包含 `\0`、`\r`、`\n`（含换行或 NUL 直接判定该候选输出被污染，回退下一个候选）；
     - 按 `:` 切分各个片段：
       - 忽略空片段（兼容首尾或连续 `:`，如 `PATH="$PATH:"`）；
       - **过滤并丢弃非 `/` 开头的相对路径片段**（如 `.`、`./node_modules/.bin`、未展开的 `~/bin`——sidecar 工作目录与用户终端不同，相对路径本身无意义，丢弃并记录日志即可，**不能因为存在相对路径条目而整段拒绝用户的 `PATH`**）；
       - 保留以 `/` 开头的绝对路径片段（允许路径内含空格，如 macOS 的 `/Applications/Visual Studio Code.app/Contents/Resources/app/bin`）；
     - 若过滤后剩余至少 1 个绝对路径片段，按 `:` 重新拼接并通过 `OsStringExt::from_vec` 转为 `OsString` 返回；若过滤后为空，则判定当前候选失败并尝试下一个候选。

### 5.2 模块内部结构

为保证所有核心逻辑在单元测试中无需依赖全局静态状态、宿主真实 `/etc/passwd` 或修改进程环境变量（避免 `cargo test` 并行竞态），模块显式提供可注入的纯函数 seam：

```text
login_shell
├── path()                              # 进程级唯一入口，内部调用 cached_path(&CACHE, resolve_path)
├── inject_path(&mut Command)           # 若 path() 为 Some 则注入 command.env("PATH", ...)
├── cached_path(cache, init)            # 接收 &OnceLock<Option<OsString>> 与初始化闭包（供单测用局部 OnceLock 验证只执行一次）
└── unix
    ├── should_probe(raw_env: Option<&str>, is_debug: bool) -> bool
    │                                   # release 默认 true；debug 默认 false（除非 raw_env 为 "1"/"true"）
    ├── resolve_path() -> Option<OsString>
    │                                   # 生产入口：读环境变量与账户 shell，调用 resolve_path_from
    ├── resolve_path_from(candidates: &[PathBuf], total_budget: Duration) -> Option<OsString>
    │                                   # 可注入候选列表与总预算，供单元测试直接调用
    ├── login_shell_candidates() -> Vec<PathBuf>
    │                                   # 生产入口：调用 login_shell_candidates_for(account_shell().as_deref())
    ├── login_shell_candidates_for(account_shell: Option<&Path>) -> Vec<PathBuf>
    │                                   # 纯函数：校验 POSIX 账户 shell（zsh/bash/sh）+ 追加系统 shell 并去重
    ├── account_shell() -> Option<PathBuf>
    │                                   # getpwuid_r（处理 ERANGE 扩容与空路径校验）
    ├── probe_shell(shell: &Path, timeout: Duration) -> Result<OsString, ProbeError>
    │                                   # 单候选：setsid spawn、超时/退出轮询、进程组回收
    ├── spawn_stdout_reader(stdout)     # detached 读取线程：匹配双端完整标记、256KiB 上限、忽略 send 错误并持续排水
    ├── extract_framed_path(raw: &[u8]) -> Result<OsString, ProbeError>
    │                                   # 提取两个 \0<DELIMITER>\0 之间的片段并调用 sanitize_path_bytes
    ├── sanitize_path_bytes(raw: &[u8]) -> Result<(OsString, usize, usize), ProbeError>
    │                                   # 校验无 \0/\r/\n，按 : 过滤相对路径、保留绝对路径并统计保留/丢弃条目数
    ├── resolve_timeout_budget() -> Duration
    │                                   # 读 std::env::var(TIMEOUT_ENV) 后调用 parse_timeout_budget
    ├── parse_timeout_budget(raw: Option<&str>) -> Duration
    │                                   # 纯函数：解析 500..=30000 范围，非法或 None 回退 5000ms
    ├── candidate_timeout(total: Duration, remaining: Duration) -> Option<Duration>
    │                                   # 纯函数：remaining < 1000ms 返回 None，否则返回 min(remaining, max(3000ms, total / 2))
    └── kill_group(pid: u32)            # libc::kill(-pid, SIGKILL)
```

### 5.3 超时与启动预算

环境变量统一使用 `TOGOSPACE_LOGIN_SHELL_*` 前缀，采用明确的两段式超时预算分配：

| 参数 | 规则 / 默认值 | 说明 |
|------|---------------|------|
| 默认总预算 `total` | `5000ms` | 所有候选共享；耗尽后停止尝试并回退继承环境 |
| 单候选预算 `per` | `min(remaining, max(3000ms, total / 2))` | 默认 `total = 5000ms` 时第一个候选最多 `3000ms`，为后备系统 shell 留出 `2000ms`；当调大 `total`（如 `10000ms`）时，第一个候选上限同步放宽至 `5000ms` |
| 最低剩余阈值 | `1000ms` | 若进入下一个候选前 `remaining < 1000ms`，`candidate_timeout` 返回 `None`，不再启动新候选，直接结束并回退 |
| 调试超时变量 | `TOGOSPACE_LOGIN_SHELL_TIMEOUT_MS` | **仅用于终端排障 / 调试**（如从命令行启动 `.app` 或 `launchctl setenv` 复现慢 rc 问题，GUI 默认启动不读取 shell 环境故不感知该变量）；合法范围 `500`–`30000`，非法值回退默认并记录警告 |
| 调试强制探测变量 | `TOGOSPACE_LOGIN_SHELL_PROBE` | 设为 `1` 或 `true` 时，在 `debug_assertions`（`tauri dev`）下强制开启登录 shell 探测 |

此外，在等待子进程输出的轮询循环中每 `50ms` 检查一次 `crate::is_quitting()`；若应用正在退出则立即终止探针进程组并返回 `None`，同时在 `backend::start` 执行 `command.spawn()` 前检查 `crate::is_quitting()`，避免退出期间误拉起子进程。

### 5.4 日志与可观测性

使用现有 `logger::log("shell", ...)` 写入 `~/.togospace/logs/desktop/desktop.log`：

- 成功：记录命中的 shell 路径、保留条目数及丢弃的非绝对路径条目数（如 `desktop login shell: resolved PATH from "/bin/zsh" (kept 12 entries, dropped 1 non-absolute)`）；
- 跳过非 POSIX 账户 shell：记录 `desktop login shell: skipping non-POSIX account shell "/opt/homebrew/bin/fish"`；
- 单个候选失败：记录 shell 路径与原因（`spawn error` / `exit code` / `signal` / `timeout` / `invalid or unframed PATH` / `output exceeded limit`）；
- 全部失败：记录 `desktop login shell: all candidates failed, falling back to inherited PATH`。

### 5.5 Windows、终端启动与开发模式（`tauri dev`）

- **Windows**：`#[cfg(not(unix))]` 直接返回 `None`，不启动 shell。
- **终端直接运行 `backend_main.py` / `desktopEntry.py`**：不经过 Tauri 外壳，完全不受影响。
- **开发模式（`tauri dev` / `#[cfg(debug_assertions)]`）**：
  - `tauri dev` 从终端启动时，进程继承的 `PATH` 已经是完整的，且可能包含开发者在当前终端会话临时激活的环境（如 `direnv`、`nix develop`、`conda activate`、自定义 `PATH`）；
  - 若无条件用登录 shell 的 `PATH` 覆盖，反而会丢失当前终端会话的临时 `PATH`；
  - 因此 **`debug_assertions` 构建默认跳过探测（`should_probe` 返回 `false`，直接沿用终端继承的 `PATH`）**；如需在 `tauri dev` 下验证登录 shell 探测逻辑，可通过设置 `TOGOSPACE_LOGIN_SHELL_PROBE=1` 强制启用。

## 6. 风险、边界与已知限制

1. **启动延迟与阻塞边界**：探测在后台线程运行，**不阻塞 Tauri 主线程（UI 与 splash 正常响应）**；`setup()` 异步预热可与 `splashscreen` 加载重叠一部分时间，但在最坏情况下（所有候选超时）最多会推迟 sidecar 启动 `5000ms`，超时回退后继续正常启动 sidecar。
2. **执行用户交互式 rc 的固有副作用**：每次桌面端冷启动都会在无 TTY、`stdin` 关闭的环境下实际执行一次用户的交互式登录配置文件（`~/.zprofile`、`~/.zshrc`、`~/.bash_profile`、`~/.bashrc` 等）。`DISABLE_AUTO_UPDATE=true` 等变量只能拦截 oh-my-zsh / zsh-tmux 等常见插件的阻塞行为；若用户在 rc 中编写了其他重度副作用（如网络拉取、工具自动更新、写本地缓存），只能依赖超时预算兜底。这是登录 shell 探测方案的固有代价。
3. **仅同步 `PATH` 的已知限制**：本期刻意只同步 `PATH`，不同步其他环境变量。这意味着 `docker`、`git`、`node`、`pnpm`、`brew` 等可执行文件可以被正常定位，但依赖配套环境变量的工具链（如 `JAVA_HOME`、`NVM_DIR`、`PNPM_HOME`、`GOPATH`、`HTTP_PROXY` / `HTTPS_PROXY`）仍不在桌面后端环境中。后续若出现明确需求，再按白名单方式扩展。
4. **非绝对路径条目会被自动丢弃**：若用户在 rc 中配置了相对路径条目（如 `PATH=.:$PATH` 或 `PATH=./node_modules/.bin:$PATH`），清洗逻辑会丢弃相对路径片段、保留其余以 `/` 开头的绝对路径片段（不会整段拒绝该候选 shell）。
5. **非 POSIX shell**：若用户的账户 shell 是 `fish`、`nushell`、`csh`、`tcsh`，账户 shell 会被跳过并回退到 `/bin/zsh`、`/bin/bash`、`/bin/sh`。如果用户只在 `config.fish` / `config.nu` 中维护了自定义路径而未写入 `~/.profile` / `~/.zprofile` / `~/.zshrc`，回退到的系统 shell 将只能读到系统与 POSIX 配置文件中的 `PATH`。
6. **后台子进程与 detached 排水线程**：rc 启动的后台进程若脱离探针进程组并长期持有 stdout，detached 排水线程会在后台持续读取直到进程结束，最坏在应用生命周期内滞留一个阻塞读取线程，但不会阻塞主流程返回。
7. **缓存策略（失败或修改 rc 后需重启应用）**：`OnceLock<Option<OsString>>` 在单个应用进程生命周期内只探测一次，无论成功或失败均缓存结果；托盘「重启后端」不会重新执行登录 shell 探测，修改 rc 文件或修复卡死问题后需重启桌面应用。

## 7. 测试与验收

### 7.1 Rust 单元测试

在 `login_shell.rs` 内用 `#[cfg(test)]` 覆盖（全部通过 §5.2 暴露的纯函数 / 可注入 seam 测试，不读写进程全局环境变量、不依赖宿主账户配置）：

- **超时配置与两段式分配（`parse_timeout_budget` / `candidate_timeout`）**：
  - `None` 返回默认 `5000ms`，首候选 `3000ms`；
  - 合法覆盖（如 `Some("10000")`）返回 `10000ms`，首候选 `5000ms`；
  - `remaining < 1000ms` 时 `candidate_timeout` 返回 `None`；
  - 非法值（越界、非数字、空串）回退 `5000ms`。
- **开发/生产探测开关（`should_probe`）**：
  - `is_debug = false` 时始终为 `true`；
  - `is_debug = true` 时默认为 `false`，传入 `Some("1")` 或 `Some("true")` 时为 `true`。
- **候选构造（`login_shell_candidates_for(account_shell: Option<&Path>)`）**：
  - POSIX 账户 shell（`zsh` / `bash` / `sh`）排第一且与系统 shell 按完整路径去重；
  - 非 POSIX 账户 shell（如 `/opt/homebrew/bin/fish`、`/bin/tcsh`）被过滤跳过；
  - `None` 或空路径时只返回系统 shell 列表。
- **定界符提取与 `PATH` 分段清洗（`extract_framed_path` / `sanitize_path_bytes`）**：
  - 忽略定界符前后的 rc 杂音；
  - 单侧定界符、空输出、含换行 `\n` / `\r` 或 `\0`、超出 `256 KiB` 缓冲区上限均判失败；
  - 混合相对路径与绝对路径（如 `.:./node_modules/.bin:/opt/homebrew/bin:/usr/bin:`）时，自动丢弃 `.` 与 `./node_modules/.bin` 及空段，保留 `/opt/homebrew/bin:/usr/bin`；
  - 全部为相对路径（如 `.:./bin`）时判失败；
  - 支持单个绝对路径（无 `:`）与含空格绝对路径（如 `/Applications/Visual Studio Code.app/Contents/Resources/app/bin`）。
- **单候选成功与失败回退（`resolve_path_from`）**：
  - 模拟 rc 导出自定义 `PATH` 被正确提取；
  - 候选不存在、非零退出、输出全为非法内容时，依次记录并回退到下一个候选。
- **提前完成与 detached 排水（防卡死）**：
  - 模拟命令在输出第二个定界符后通过新会话启动后台长进程（如 `python3 -c 'import os,time; os.setsid(); time.sleep(100)'`）持续持有 stdout，验证 `probe_shell` 仍在 `100ms` 宽限期内返回结果、reader 线程忽略后续关闭且无僵尸进程。
- **超时与进程组清理**：
  - 候选 `sleep` 超时后返回 `timeout`（同时验证 reader 线程在 `Receiver` 被 drop 后 `send` 失败不会 panic），探针进程组被 `SIGKILL` 清理，耗时有界。
- **缓存与注入契约（`cached_path` / `inject_path`）**：
  - 使用局部 `OnceLock::new()` 调用 `cached_path`，验证无论首次返回 `Some` 还是 `None`，初始化闭包都只执行一次；
  - 验证注入 `Command` 后 `cmd.get_envs()` 中的 `PATH` 等于期望值。
- **非 Unix 编译期与契约检查**：
  - `#[cfg(not(unix))]` 分支下 `path()` 返回 `None`（注：在 macOS/Linux 下运行 `cargo test` 时该分支不参与编译，属于 Windows target 的编译与测试检查项）。

注意：默认 `./scripts/run_tests.sh` 只跑 Python 测试，Rust 测试需要单独执行 `cargo test`（工作目录 `tauri/src-tauri`），建议在桌面构建检查清单中补充。

### 7.2 手工验收

1. 从 Finder / Dock 启动 `.app`，检查 `~/.togospace/logs/desktop/desktop.log` 中出现 `desktop login shell: resolved PATH`；
2. 在应用的 Agent 工具调用中执行 `which docker`、`which node`、`which git`，结果与系统终端一致；
3. 在 `~/.zshrc` 中加入 `sleep 6`，确认探测在 5s 预算内超时返回、应用正常完成启动，日志记录 `timeout`；
4. 运行 `tauri dev` 确认默认跳过探测（保留当前终端 `PATH`），运行 `TOGOSPACE_LOGIN_SHELL_PROBE=1 npm run dev` 确认可触发探测；
5. 从终端直接运行 `backend_main.py`，确认行为不变。

## 8. 实施任务拆分

| 序号 | 任务 | 产出 |
|------|------|------|
| 1 | 新增 `tauri/src-tauri/src/login_shell.rs`，实现 Unix `PATH` 读取、分段清洗与非 Unix 空实现 | 模块 + 单元测试 |
| 2 | 在 `tauri/src-tauri/src/lib.rs` 声明模块并在 `setup` 中异步预热 | 模块接入 |
| 3 | 在 `tauri/src-tauri/src/backend.rs::start` 调用 `login_shell::inject_path(&mut command)` | sidecar 获得 `PATH` |
| 4 | 执行 `cargo test` / `cargo check`（工作目录 `tauri/src-tauri`），确认测试通过且无编译告警 | 测试与构建结果 |
| 5 | 更新 `tauri/README.md` 补充桌面端 `PATH` 读取与已知限制说明 | 文档 |
