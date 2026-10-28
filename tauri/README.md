# TogoSpace 桌面外壳（Tauri）

Tauri v2（Rust）负责窗口、启动画面、托盘、子进程与日志汇聚；Python 后端由 PyInstaller
以 **onedir** 形态打包，作为 bundle resources 放在 `Resources/backend/`。

设计文档：`docs/design/2026-10-29-tauri-desktop-shell/`

## 目录结构

```text
tauri/
├── package.json             # @tauri-apps/cli
├── scripts/
│   ├── build_backend.py     # PyInstaller 打包后端 → tauri/build/backend
│   ├── build_desktop.py     # 前端 / 后端 / app / 签名 / DMG / 公证 / 检查
│   └── entitlements.mac.plist
├── src-tauri/
│   ├── src/
│   │   ├── lib.rs           # 启动门控、窗口、IPC 命令、退出流程
│   │   ├── backend.rs       # 子进程、stdout 协议、状态机
│   │   ├── config.rs        # desktop.json（端口 + token，0600）
│   │   ├── logger.rs        # desktop.log（10MB × 3）
│   │   ├── tray.rs          # 托盘菜单
│   │   ├── sys/             # 平台差异（macOS / Windows / Linux）
│   │   └── utils.rs         # stdio 逐行读取、URL 生成
│   ├── capabilities/default.json
│   └── tauri.conf.json
└── build/                   # 构建产物（gitignore）
```

## 开发

前置：Rust 工具链、Node.js、仓库 `.venv`（Python 3.11+）。

```bash
cd tauri
npm install
npm run dev          # 启动 Vite(8181) + Tauri 窗口 + Python 后端（debug 构建直接跑 src/desktopEntry.py）
```

debug 构建下后端命令为 `.venv/bin/python3 src/desktopEntry.py`；release 构建下为
`Resources/backend/TogoSpace-backend`。

## 构建

```bash
# 全流程（前端 → 后端 → .app → 签名 → DMG → 公证）
python3 tauri/scripts/build_desktop.py

# 分步
python3 tauri/scripts/build_desktop.py --action frontend
python3 tauri/scripts/build_desktop.py --action backend
python3 tauri/scripts/build_desktop.py --action app
python3 tauri/scripts/build_desktop.py --action sign,zip
python3 tauri/scripts/build_desktop.py --action notarize
python3 tauri/scripts/build_desktop.py --action check

# 指定架构（PyInstaller 不支持交叉编译，目标架构需要对应的 Python 环境）
python3 tauri/scripts/build_desktop.py --arch x86_64
```

- 前端只构建一次：`frontend/dist` 给 Tauri，`assets/frontend` 给后端浏览器形态，脚本做哈希一致性校验。
- 版本号唯一来源：`src/version.py`，脚本同步 `tauri.conf.json` / `Cargo.toml` / `tauri/package.json`。
- 签名/公证配置复用 `scripts/build_config.json`（`apple_id` / `app_specific_password` /
  `team_id` / `signing_identity_hash`）。
- 产物：`dist/TogoSpace_<version>_<arch>.dmg`。

### CI

`.github/workflows/tauri.yml` 在 `v*` tag 和手动触发时构建 macOS 双架构 DMG：

- 产物上传为 workflow artifact（`macOS-dmg-arm64` / `macOS-dmg-x86_64`），不创建 Release；
  GitHub Release 仍由 `release.yml` 负责。
- 配置了 Apple secrets 时执行签名 + 公证；未配置时只构建未签名 DMG，用于验证构建链路。
- x86_64 在 Apple Silicon runner 上通过 Rosetta 2 + uv 安装 x86_64 Python 依赖
  （PyInstaller 不支持交叉编译）。

## 运行时

| 路径 | 内容 |
|------|------|
| `~/.togospace/setting.json` | 后端运行配置（Python 拥有） |
| `~/.togospace/desktop.json` | 端口 + 访问 token（Rust 拥有，0600） |
| `~/.togospace/logs/backend/` | 后端日志 |
| `~/.togospace/logs/desktop/desktop.log` | 外壳 + 后端 stdio + 前端 console 汇聚日志 |

开发模式可用 `STORAGE_ROOT` 环境变量覆盖数据目录。

### 启动状态机

后端通过 stdout 单行 marker 汇报阶段：

```text
[TOGO] MIGRATION_START
[TOGO] MIGRATION_END
[TOGO] READY url=http://127.0.0.1:<port>
[TOGO] ERROR code=<n> message=<text>
```

`NotStarted → Starting → (Migrating → Starting)* → Ready`，15 秒未就绪进入 `Failed`。
启动画面（`frontend/splash.html`）由 `backend-migration-start/end`、`backend-error` 事件驱动。

### 进程生命周期

Rust 持有后端子进程的 stdin。退出时关闭 stdin → 后端读到 EOF 调用 `request_shutdown()`；
5 秒内未退出则强杀。父进程被 `kill -9` 时 OS 关闭管道，后端同样会自行停机，不产生孤儿进程。

## 测试

```bash
cd tauri/src-tauri && cargo test
cd frontend && npm run test:run
./scripts/run_tests.sh
```

## 发布说明（面向用户）

- 桌面模式下后端只监听 `127.0.0.1`，并强制开启 token 鉴权。浏览器访问
  `http://127.0.0.1:<port>` 时需要从托盘菜单「复制访问 Token」获取 token。
- 数据目录沿用 `~/.togospace`，老用户不需要迁移。
- 关闭主窗口只是隐藏到托盘；`Cmd+Q`、托盘「退出」或启动画面「确认退出」才会停止后端。
- 首次启动会生成 `~/.togospace/desktop.json`（权限 0600），删除后下次启动会重新生成端口与 token。

## 平台状态

- macOS：完整支持（开发、构建、签名、公证、DMG）。
- Windows / Linux：`sys/` 分支与构建入口已预留，本期不作为发布门槛。
