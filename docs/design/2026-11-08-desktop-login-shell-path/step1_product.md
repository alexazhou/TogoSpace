# 桌面端登录 shell PATH 读取 - 产品文档

> 关联文档：[技术设计](./step2_technical.md) | 专项调研：[DSH 实现分析与 TogoSpace 移植方案](../2026-10-29-tauri-desktop-shell/login-shell-environment_design.md)
> 所属：V28 Tauri 桌面外壳后续增强

## 1. 背景与目标

### 1.1 现状与痛点

macOS / Linux 的图形程序从 Dock、Finder 或应用菜单启动时，只继承会话管理器（launchd / systemd user session）提供的环境，不会执行用户的 shell 启动文件。因此 TogoSpace 桌面端存在以下链路：

- Tauri 外壳进程拿到的是最小 `PATH`（通常只有 `/usr/bin:/bin:/usr/sbin:/sbin`）；
- Rust 拉起 Python sidecar 时，sidecar 继承同一份最小 `PATH`；
- 后端及其所有子进程都无法定位用户通过 Homebrew、nvm、pnpm、asdf 等安装的命令。

用户可见的表现：

- Agent 工具调用里 `which docker`、`which node`、`which git` 找不到或指向错误版本；
- `shutil.which("docker")` 失败，只有 `src/util/containerUtil.py` 对 docker 做了常见路径兜底，git / node / pnpm / brew 等没有同类兜底；
- 同一个后端从终端直接运行（`backend_main.py` / `desktopEntry.py`）时一切正常，只有桌面端异常，用户很难联想到是「启动方式」导致的。

### 1.2 本次目标

- **桌面端冷启动后，后端看到与用户登录 shell 一致的 `PATH`**：Agent 工具调用、子进程执行、`which` 类探测与用户在终端里的结果一致。
- **零配置**：不要求用户手写 `launchctl setenv`、不要求修改应用配置。
- **有界启动开销**：探测只在启动阶段执行一次，最坏推迟 sidecar 启动不超过总预算（默认 5000ms），正常情况与启动画面加载重叠。
- **不影响其他形态**：从终端运行的后端、TUI、浏览器访问、`tauri dev` 行为保持不变。
- **可观测**：探测结果、命中的 shell、降级原因都写入桌面端日志，排障不需要抓包或加日志。

### 1.3 非目标

- **不同步 `PATH` 以外的环境变量**：`JAVA_HOME`、`NVM_DIR`、`PNPM_HOME`、`GOPATH`、`HTTP_PROXY` / `HTTPS_PROXY` 等仍不在桌面后端环境中。
- **不解析非 POSIX shell 的配置**：账户 shell 为 `fish`、`nushell`、`csh`、`tcsh` 时跳过该 shell，回退到系统 POSIX shell。
- **不做 Windows 实现**：Windows 直接跳过探测，行为与现状一致。
- **不运行时刷新**：修改 rc 文件后需重启桌面应用；托盘「重启后端」不会重新探测。
- **不改动 Python 业务代码**：能力完全由 Rust 外壳注入。

## 2. 功能行为

### 2.1 桌面端冷启动

1. 用户从 Finder / Dock 启动 `.app`；
2. Tauri 在后台读取一次用户登录 shell 的 `PATH`（读取过程与启动画面加载并行）；
3. 拉起 Python sidecar 时注入该 `PATH`；
4. 后端及其所有子进程（Agent 工具调用、终端命令、容器 CLI）自然继承。

### 2.2 终端与浏览器形态

| 形态 | 行为 |
|------|------|
| 终端运行 `backend_main.py` | 完全不受影响，沿用当前终端 `PATH` |
| 终端运行 `desktopEntry.py` | 完全不受影响 |
| 浏览器访问桌面端后端 | 后端已由外壳注入 `PATH`，与桌面窗口一致 |
| TUI 前端 | 不受影响 |

### 2.3 开发模式（`tauri dev`）

- 默认**跳过探测**：`tauri dev` 从终端启动时继承的 `PATH` 已经是完整的，并且可能包含开发者当前会话的 `direnv` / `nix develop` / `conda activate` 等临时环境；用登录 shell 的 `PATH` 覆盖反而会丢失这些内容。
- 需要验证探测逻辑时，设置 `TOGOSPACE_LOGIN_SHELL_PROBE=1` 强制开启。

### 2.4 降级与边界行为

| 场景 | 用户感知 |
|------|----------|
| 账户 shell 是 `fish` / `nushell` / `csh` / `tcsh` | 跳过账户 shell，依次尝试 `/bin/zsh`、`/bin/bash`、`/bin/sh`；只在 fish 配置里维护的路径可能读不到 |
| rc 文件执行很慢或卡住 | 最多等待总预算（默认 5000ms）后继续启动，应用不阻塞；日志记录 `timeout` |
| 所有候选 shell 都失败 | 使用继承的最小 `PATH` 正常启动；docker 仍有 `containerUtil.py` 兜底；日志记录回退 |
| `PATH` 中混有相对路径条目（如 `.:$PATH`、`bin:$PATH`） | 丢弃相对条目、保留绝对条目继续使用，不会整段放弃 |
| `PATH` 条目包含空格（macOS 常见） | 正常保留 |
| 修改了 `~/.zshrc` 后希望立即生效 | 需重启桌面应用；托盘「重启后端」不重新探测 |

### 2.5 调试开关

| 环境变量 | 作用 | 说明 |
|---|---|---|
| `TOGOSPACE_LOGIN_SHELL_TIMEOUT_MS` | 调整探测总预算，合法范围 500–30000 | 仅用于终端排障（含 `launchctl setenv` 场景）；GUI 直接启动不读取 shell 环境，默认不感知 |
| `TOGOSPACE_LOGIN_SHELL_PROBE` | 设为 `1` / `true` 时在 `tauri dev` 下强制开启探测 | 仅影响 `debug_assertions` 构建 |

## 3. 验收标准

- [ ] 从 Finder / Dock 启动 `.app`，`~/.togospace/logs/desktop/desktop.log` 出现 `desktop login shell: resolved PATH from "..."`；
- [ ] 桌面端 Agent 工具调用执行 `which docker`、`which node`、`which git`，结果与系统终端一致；
- [ ] 在 `~/.zshrc` 中加入 `sleep 6`，验证探测在 5s 预算内超时返回、应用正常完成启动，日志记录 `timeout`；
- [ ] `tauri dev` 默认保留当前终端 `PATH`；`TOGOSPACE_LOGIN_SHELL_PROBE=1 npm run dev` 可触发探测；
- [ ] 从终端运行 `backend_main.py`，行为与改动前完全一致；
- [ ] 账户 shell 为 `fish` 时应用正常启动，日志记录跳过并可回退到系统 shell；
- [ ] `PATH` 含相对条目时保留绝对条目并记录丢弃数量；
- [ ] 所有候选失败时应用正常启动，日志明确记录回退到继承 `PATH`。

## 4. 使用说明

- 普通用户无需任何操作，升级后从 Dock / Finder 启动即生效。
- 排查工具找不到的问题时，先看 `~/.togospace/logs/desktop/desktop.log` 中的 `desktop login shell:` 日志：
  - `resolved PATH from "<shell>"`：命中的 shell 与保留条目数；
  - `skipping non-POSIX account shell`：账户 shell 被跳过；
  - `all candidates failed`：已回退到继承 `PATH`。
- 修改 shell 配置后，重启桌面应用即可重新读取。
