# Tauri 桌面外壳 - 开发任务表

> 状态：已完成（2026-10-29）；签名/公证脚本已实现，需本机 `scripts/build_config.json` 才能实际执行
> 标记：`[x]` 已完成，`[ ]` 待完成
> 关联：[产品文档](./step1_product.md) / [技术文档](./step2_technical.md)

## 任务概览

按依赖排序：先打通「PyInstaller onedir 能被 Tauri 拉起并 READY」这条最短链路（任务 0~3），再补前端适配（任务 4~5）、外壳体验（任务 6~7），最后做签名公证与发布（任务 8~9）。任务 1 与 2 之间有一个必须提前验证的路径假设（R1），验证不通过要回到任务 1 调整 spec。

---

## 任务列表

### 任务 0: 环境与骨架

**依赖**: 无

**核心文件**:
- `tauri/package.json`
- `tauri/src-tauri/Cargo.toml`
- `tauri/src-tauri/build.rs`
- `tauri/src-tauri/tauri.conf.json`
- `tauri/src-tauri/src/main.rs`
- `tauri/src-tauri/src/lib.rs`
- `tauri/src-tauri/.cargo/config.toml`
- `.gitignore`

**子任务**:
- [x] `tauri/` 初始化（`npm create tauri-app` 或手写最小骨架），Cargo 依赖：`tauri 2`（`tray-icon`、`image-png`）、`tauri-plugin-dialog`、`tauri-plugin-fs`、`tauri-plugin-shell`、`serde`、`serde_json`、`uuid`、`open`、`libc`（unix）
- [x] `tauri.conf.json` 按技术文档 §7 配置：splashscreen 窗口、CSP、`resources`、`beforeBuildCommand`
- [x] `.cargo/config.toml` 把 `target-dir` 指到 `tauri/build/target`，避免污染仓库根
- [x] `.gitignore` 增加 `tauri/build/`、`tauri/node_modules/`、`tauri/src-tauri/target/`
- [x] `npm run tauri dev` 能起一个空白窗口（后端尚未接入）

**验收**: `tauri dev` 打开空白窗口不报错。

---

### 任务 1: PyInstaller onedir 后端产物

**依赖**: 无（与任务 0 并行）

**核心文件**:
- `scripts/togo_desktop.spec`
- `src/desktopEntry.py`
- `src/appPaths.py`
- `tauri/scripts/build_backend.py`

**子任务**:
- [x] `src/desktopEntry.py`：读 env（`TOGOSPACE_DESKTOP` / `_PORT` / `_TOKEN`）、校验失败即 `[TOGO] ERROR` + 退出码 2
- [x] `src/desktopEntry.py`：`_apply_desktop_overrides()`（强制 `127.0.0.1`、强制 `auth.enabled`、只在值变化时落盘），先不接 `backend_main`
- [x] `scripts/togo_desktop.spec`：以 `togo_agent.spec` 为基线，改入口、`console=True`、去掉 BUNDLE、去掉 pystray/AppKit/PIL hiddenimports
- [x] **R1 验证**：打包后确认 `appPaths.ASSETS_DIR` / `PRESET_DIR` / 迁移目录都指到正确位置；必要时改 `datas` 目标为 `_internal/assets`，或在 `appPaths` 增加「可执行文件同级 `assets/`」兜底
- [x] `tauri/scripts/build_backend.py`：调 PyInstaller → 复制 `dist/TogoSpace-backend/` 到 `tauri/build/backend/`
- [x] 手工验证：直接运行产物可执行文件（`TOGOSPACE_DESKTOP=1 TOGOSPACE_DESKTOP_PORT=18180 …`），`curl http://127.0.0.1:18180/system/status.json` 返回 200

**验收**: 单机直接运行 onedir 后端，HTTP 服务可用；`assets/` 路径全部解析正确。

---

### 任务 2: backend_main 钩子与 marker

**依赖**: 任务 1（部分）

**核心文件**:
- `src/backend_main.py`
- `src/service/ormService.py`
- `src/desktopEntry.py`

**子任务**:
- [x] `backend_main.main()` 增加可选 `callbacks`（`on_migration_start` / `on_migration_end` / `on_ready`），默认 `None` 时行为完全不变
- [x] `ormService.startup()` 在「判定需要迁移」与「迁移完成」两处回调
- [x] `web_server.listen()` 成功后回调 `on_ready(host, port)`
- [x] `desktopEntry` 接上 callbacks，输出 `[TOGO] MIGRATION_START` / `[TOGO] MIGRATION_END` / `[TOGO] READY url=...`
- [x] `desktopEntry` 起 stdin EOF 线程 → `backend_main.request_shutdown()`
- [x] 桌面模式跳过 `_check_single_instance` / `_write_pid` / `_remove_pid`
- [x] `appPaths.IS_DESKTOP_MODE` + `_detect_run_env()` 增加 `desktop`
- [x] Python 单测：marker 格式、override 幂等（值相同不落盘）、stdin EOF 触发 shutdown

**验收**: `pytest tests/unit -k desktop` 通过；手工 `echo | ./TogoSpace-backend` 后进程自动退出。

---

### 任务 3: Rust 外壳托管后端

**依赖**: 任务 0、1、2

**核心文件**:
- `tauri/src-tauri/src/backend.rs`
- `tauri/src-tauri/src/config.rs`
- `tauri/src-tauri/src/utils.rs`
- `tauri/src-tauri/src/sys/*`
- `tauri/src-tauri/src/lib.rs`

**子任务**:
- [x] `config.rs`：`desktop.json` 读写、默认值、0600 权限、token 生成、端口选择（占用则挑空闲端口并写回）
- [x] `utils.rs`：`watch_piped_stdio`（两路管道必读）、`generate_client_url`
- [x] `sys/`：macOS / Windows / Linux 的 backend 路径拼接与 `setup_stdio` / `post_spawn`（Windows 加 `CREATE_NO_WINDOW`）
- [x] `backend.rs`：状态机（`NotStarted/Starting/Migrating/Ready/Failed/Exited`）+ 三个输入源 + generation 超时（15s）
- [x] `backend.rs`：stdout 行协议解析，非法/乱序事件丢弃并打日志
- [x] `lib.rs`：启动门控（`setup_finished && splash_loaded` 后才 spawn）、`emit` 四个事件、`Ready` 后关 splash 开主窗口
- [x] `lib.rs`：关闭窗口 → 隐藏 + Dock 隐藏；`Reopen` → 重新显示/recreate
- [x] `lib.rs`：`RunEvent::ExitRequested` prevent_exit
- [x] Rust 单测：`watch_piped_stdio` 灌满管道（复用参考实现 `stdio_test.rs`）、行协议容错、`generate_client_url`、`desktop.json` 回落

**验收**: `tauri dev` 下启动画面出现 → 后端起来 → 主窗口出现；杀后端进程后启动画面/托盘给出错误提示。

---

### 任务 4: 前端运行时配置注入

**依赖**: 任务 3

**核心文件**:
- `frontend/src/utils/platform.ts`
- `frontend/src/main.ts`
- `frontend/src/api.ts`
- `frontend/src/authStore.ts`
- `frontend/src/i18n.ts`
- `frontend/src/router.ts`
- `frontend/splash.html`
- `frontend/src/splash.ts`
- `frontend/vite.config.ts`
- `frontend/package.json`

**子任务**:
- [x] `utils/platform.ts`：`isTauri()` / `openExternal()` / `saveTextFile()`
- [x] `api.ts`：`const API_BASE_URL` → `let runtimeBaseUrl` + `setApiBaseUrl()`（技术文档 §6.1.2）
- [x] `api.ts::makeWsUrl`：判定条件改为「运行时 base 非空」，从 base 派生 `ws://`；**不可照抄参考实现（其前端无 WebSocket）**（§6.1.3）
- [x] `authStore.ts`：补 memory-first 层 + `setToken(token, { persist })`，否则 `persist:false` 会静默失效（§6.1.4）
- [x] `main.ts`：拆 `bootstrap()`，`loadDesktopRuntime()` → `syncLanguageFromBackend()` → `mount()`，保证注入早于首次 `/system/status.json`（§6.1.5）
- [x] `main.ts`：`console.log` 覆写转发 `log_to_rust`（§3.8）
- [x] 未授权弹窗：确认桌面模式注入后 `showTokenDialog` 不被误触发；401 拦截保留（§6.1.5）
- [x] `i18n.ts`：`syncLanguageFromBackend` 改用 `getSystemStatus()`，消除裸 `fetch`
- [x] `router.ts`：`createWebHashHistory()`
- [x] `splash.html` + `splash.ts`：spinner / 迁移文案 / 错误态 / 退出按钮；监听三个事件
- [x] `vite.config.ts`：双入口（main + splash）、`remove-crossorigin` 插件、`modulePreload: false`；dev 端口保持 8181
- [x] `package.json` 增加 `@tauri-apps/api`，`scripts` 增加 `build:tauri`
- [x] 导出 JSON（`SettingsPage.vue`）、外链（`<a target="_blank">` 四处）切到 `platform.ts`
- [x] 前端单测：`isTauri` 分支、`setApiBaseUrl` 生效、`makeWsUrl` 在桌面 base 下产出 `ws://127.0.0.1:<port>/ws/events.json`、桌面模式不写 localStorage

**验收**: `npm run build:tauri` 产物在 Tauri 窗口中可完整操作（团队/房间/消息/设置/导出/外链）。

---

### 任务 5: 后端跨源与桌面安全边界

**依赖**: 任务 2

**核心文件**:
- `src/constants.py`
- `src/controller/baseController.py`
- `src/controller/wsController.py`
- `src/desktopEntry.py`
- `docs/setting.README.md`（或 assets 中的对应文档）

**子任务**:
- [x] `constants.DESKTOP_ALLOWED_ORIGINS`
- [x] `BaseHandler.set_default_headers()`：白名单回填 ACAO / ACAH / ACAM + `Vary: Origin`
- [x] 预检：确保 `OPTIONS` 返回 204（`SUPPORTED_METHODS` 或统一拦截，注意不要影响既有 handler）
- [x] `EventsWsHandler.check_origin()`：白名单放行，其余沿用默认拒绝
- [x] 桌面模式强制 `bind_host=127.0.0.1` + `auth.enabled=true` + token 注入后落盘
- [x] `setting.README.md` 说明「桌面模式下 `bind_host` / `auth` 会被覆盖」
- [x] API/集成测试：ACC 头、预检 204、WS 白名单、桌面 token 401/200

**验收**: `tests/api` 中新增用例全绿；桌面窗口下所有请求与 WS 正常，`Origin: http://evil.example` 的 WS 被拒。

---

### 任务 6: 托盘与 IPC 命令

**依赖**: 任务 3

**核心文件**:
- `tauri/src-tauri/src/lib.rs`
- `tauri/src-tauri/src/tray.rs`
- `tauri/src-tauri/capabilities/default.json`
- `tauri/src-tauri/icons/`

**子任务**:
- [x] 托盘图标（template png）+ 菜单：显示窗口 / 在浏览器中打开 / 复制访问 Token / 打开配置目录 / 打开日志目录 / 退出
- [x] 退出流程：请求后端优雅停机（关 stdin → 等 ≤5s → `kill`）→ `app.exit(0)`
- [x] 后端 `Failed`/`Exited` 时托盘图标告警 + 菜单「查看错误」；「重试启动」走 `restart_backend`
- [x] IPC 命令：`get_backend_url` / `get_auth_token` / `get_backend_state` / `restart_backend` / `open_external` / `save_text_file` / `exit_app` / `log_to_rust`
- [x] `capabilities/default.json` 最小权限（窗口 `main` + `splashscreen`）
- [x] `tauri-plugin-single-instance`（D3）
- [x] 应用图标集（`tauri icon` 生成）、`Info.plist` 版本号与 `src/version.py` 同步

**验收**: 托盘菜单全部可用；二次启动不产生第二个后端。

---

### 任务 7: 日志汇聚

**依赖**: 任务 3

**核心文件**:
- `tauri/src-tauri/src/logger.rs`

**子任务**:
- [x] `~/.togospace/logs/desktop/desktop.log`，10MB × 3 轮转
- [x] 后端 stdout/stderr 逐行写入，带 `[backend:out]` / `[backend:err]`
- [x] 外壳日志带 `[shell]` + 相对启动时间戳
- [x] 前端 `console.log` → `log_to_rust` → 同一文件
- [x] 托盘「打开日志目录」

**验收**: 一次完整启动+退出后，`desktop.log` 能串起「外壳 → 后端 → 前端」全时间线。

---

### 任务 8: 构建脚本与签名公证

**依赖**: 任务 1、3、4、6

**核心文件**:
- `tauri/scripts/build_desktop.py`
- `tauri/scripts/entitlements.mac.plist`
- `scripts/build_config.json`（复用现有配置项）

**子任务**:
- [x] `build_desktop.py`：`--action frontend,backend,app,sign,notarize,zip` 分步；默认全流程
- [x] 前端单源构建 + 双份复制（Tauri `frontendDist` / 后端 `assets/frontend`）+ **哈希一致性校验**（R8）
- [x] 版本号统一读取 `src/version.py`，写入 `tauri.conf.json` 的 `version`
- [x] macOS 签名顺序：内层 Mach-O → `Contents/MacOS/TogoSpace` → 外层 `.app` → `--verify --deep --strict`
- [x] entitlements：网络客户端、JIT、未签名可执行内存、用户选择文件读写（`allow-jit` 对 Python 依赖必要）
- [x] dmg 生成 + 签名 + `notarytool submit --wait` + `stapler staple`
- [x] 架构参数 `--arch arm64|x86_64`，产物命名 `TogoSpace_<version>_<arch>.dmg`
- [x] `--action check`：打印签名链、Gatekeeper 评估结果

**验收**: 干净机器（无 Python、未装过 app）安装 → 打开无 Gatekeeper 警告 → 全功能可用。

---

### 任务 9: 文档与收尾

**依赖**: 任务 5、8

**核心文件**:
- `tauri/README.md`
- `docs/tech/10_release/packaging_design.md`
- `CLAUDE.md`（仓库结构、启动命令）
- `docs/design/ROADMAP.md`

**子任务**:
- [x] `tauri/README.md`：目录结构、dev/build 命令、dev vs prod 差异、常见问题（R1/R4）
- [x] `packaging_design.md` 增加「桌面形态（Tauri）」章节并标注 legacy 托盘形态状态（D1）
- [x] `CLAUDE.md` 补充 `tauri/` 目录说明与构建命令
- [x] ROADMAP 增加本里程碑条目与文档索引
- [x] 发布说明：桌面模式强制鉴权、数据目录沿用 `~/.togospace`、浏览器访问需 token

**验收**: 新人按 `tauri/README.md` 能在本地跑起 `tauri dev` 并打出 dmg。

---

## 测试检查清单

### 单元测试

- [ ] Rust：`watch_piped_stdio` 管道排空、行协议解析、`desktop.json` 回落、`generate_client_url`
- [ ] Python：desktop 参数校验、override 幂等与强制项、marker 格式、stdin EOF → shutdown

### 集成 / API 测试

- [ ] 跨源 ACAO / 预检 204
- [ ] WS origin 白名单（tauri 通过、任意外部 origin 拒绝）
- [ ] 桌面 token 鉴权（无 token 401、错误 token 401、正确 token 200）

### 手工验收（每个 RC）

- [ ] 全新机器安装 → 冷启动 → 启动画面 → Quick Init → 正常使用 → 退出
- [ ] 老 `~/.togospace` 升级 → 迁移提示可见 → 数据完整
- [ ] 关窗隐藏 / 托盘唤起 / Dock 唤起 / `Cmd+Q` 退出
- [ ] 主程序 `kill -9` → 3s 内后端退出，无孤儿进程
- [ ] 端口被占用 → 自动换端口，托盘显示新 URL，浏览器可打开
- [ ] **实时链路**：桌面窗口内 Agent 新消息/状态变更**无需刷新**即可出现（验证 `makeWsUrl` 走了运行时 base，非 `tauri.localhost`）
- [ ] 启动全过程无「输入 Token」弹窗闪现（验证注入早于首次 `/system/status.json`）
- [ ] 非法 `setting.json` / 数据库损坏 → 启动画面给出错误与退出按钮
- [ ] 断网启动 → 主窗口可用，LLM 报错可读
- [ ] 浏览器形态（`http://127.0.0.1:<port>`）输入 token 后功能完整
