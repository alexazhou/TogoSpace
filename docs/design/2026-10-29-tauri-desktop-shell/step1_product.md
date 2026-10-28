# Tauri 桌面外壳 - 产品文档

> 状态：方案设计（未开发）
> 关联：`docs/tech/10_release/packaging_design.md`（现有 PyInstaller + 系统托盘方案）

## 1. 背景

当前桌面形态是一条「Python 全包」路线：

- `src/appEntry.py` 用 pystray 建系统托盘，后端 Tornado 跑在同一进程的子线程里
- `scripts/build_mac.py` / `scripts/togo_agent.spec` 用 PyInstaller 打成 `TogoSpace.app`
- 窗口能力完全依赖外部浏览器：托盘菜单「打开 Web 界面」`webbrowser.open("http://localhost:8180")`

这条路线能跑，但有几个结构性短板：

| 问题 | 现状 | 影响 |
|------|------|------|
| 没有真正的应用窗口 | 只能跳浏览器 | 无法做启动画面、窗口状态、Dock/任务栏集成，用户感知更像「后台服务 + 网页」 |
| 托盘与后端同进程 | pystray + Tornado 同进程多线程 | 托盘崩溃会带走后端；后端初始化卡住时托盘无反馈；macOS 上 PyObjC 与 asyncio 混用易踩坑 |
| 打包/签名链路绑死 Python | PyInstaller 直接产出 `.app` | PyInstaller 自带的 bootloader 与 bundle 结构在加固运行时、公证下更容易出问题，且升级只能整包换 |
| 前端只作为静态资源被后端托管 | `assets/frontend/` 由 Tornado 托管 | 桌面端无法使用任何原生能力（打开外链、保存文件、系统通知） |

参考项目 `serverless_ai_gateway` 的形态更贴近我们要的样子：**Rust Tauri 主程序 + 打包后的后端 sidecar**，Tauri 负责窗口 / 托盘 / 生命周期 / 启动画面，后端只提供 HTTP + WebSocket。本次把那条链路的 Node sidecar 换成 Python sidecar。

## 2. 目标

最终交付物是**一个**安装包（`.dmg` / `.msi` / `.AppImage`），内部由两块组成：

1. **Rust Tauri 程序**：应用外壳，拥有窗口、托盘、启动画面、子进程管理、日志汇聚
2. **PyInstaller 打包后的 Python 后端**：纯后端（无托盘、无 GUI），由 Tauri 拉起，只监听 `127.0.0.1`

### 2.1 功能目标

- 双击图标 → 启动画面（启动中 / 数据库升级中 / 启动失败三种状态）→ 主窗口加载内置前端
- 主窗口内即完整产品：团队、房间、消息、设置全部可用，实时事件走 WebSocket
- 关窗不退出（隐藏到托盘），托盘菜单可重新打开窗口 / 打开配置目录 / 退出
- 退出应用时后端必定一起退出（含 Tauri 被强杀的场景，不留孤儿进程）
- 后端崩溃 / 启动超时，启动画面给出可读错误，可一键退出
- 首次启动零配置即可打开界面，并引导完成 LLM 初始化（复用现有 Quick Init 流程）
- 用户数据默认沿用 `~/.togospace`，老用户升级后原有团队、房间、消息、配置全部还在
- 浏览器形态继续可用：后端仍然托管一份前端构建产物，可继续用浏览器访问 `http://127.0.0.1:<port>`

### 2.2 非目标

- 不重写前端框架（仍是 Vue 3 + Vite），不引入 Node 侧后端
- 不在本期实现应用内自动更新（Tauri updater 留作后续；仍沿用「检查更新 → 打开 Release 页」）
- 不改变后端四层架构与业务逻辑，只增加「桌面运行模式」的启动/安全/边界适配
- 不在本期改造 TUI 前端
- 不追求 Windows / Linux 的完整发布（本期先做 macOS 全链路：构建、签名、公证；Windows/Linux 保留结构与脚本入口）

## 3. 与现有形态的关系

| 形态 | 入口 | 是否保留 |
|------|------|----------|
| 源码开发后端 | `src/backend_main.py` | 保留，行为不变 |
| 源码开发 Web 前端 | `frontend/` + Vite | 保留 |
| 终端前端 | `tui/tui_main.py` | 保留 |
| 浏览器 + 菜单栏托盘 | `src/appEntry.py`（PyInstaller `.app`） | 保留一段时间，标记为 legacy；桌面外壳稳定后停止发布 |
| **桌面应用（本方案）** | `tauri/`（Rust）+ `src/desktopEntry.py`（Python sidecar） | 新增，作为主发布形态 |

## 4. 用户场景

### 4.1 首次启动（未配置 LLM）

1. 双击 `TogoSpace.app`
2. 启动画面显示「正在启动中…」
3. 后端就绪 → 启动画面关闭 → 主窗口打开
4. 前端读取 `/system/status.json` 发现 `initialized=false`，弹出 Quick Init 引导
5. 用户填完 LLM 服务 → 保存 → 调度恢复

### 4.2 老用户升级

1. 老版本数据在 `~/.togospace/`（setting.json / data/data.db / workspace）
2. 新版首次启动直接复用该目录，启动画面短暂显示「数据库升级中，请等待」，执行迁移
3. 迁移完成后进入主窗口，历史数据完整

### 4.3 后端启动失败

- 端口不可用、`setting.json` 非法、数据库损坏等
- 启动画面切换为错误态：标题「服务启动失败」，正文给出后端原始错误摘要，按钮「确认退出」直接关闭应用
- 后端在运行期异常退出：主窗口仍保留（可看最后的界面），托盘图标变红，再次点击「显示窗口」时提示后端已停止

### 4.4 关闭窗口

- 点窗口关闭按钮 / `Cmd+W`：窗口隐藏，Dock 图标消失，托盘常驻
- 点 Dock 图标 / 托盘「显示窗口」：窗口重新出现
- 托盘「退出」或 `Cmd+Q`：后端优雅停机（等待在跑的任务收尾）后退出

### 4.5 浏览器访问

- 托盘菜单「在浏览器中打开」：用系统默认浏览器打开 `http://127.0.0.1:<实际端口>`
- 由于桌面模式强制开启 token 鉴权，浏览器首次访问需要输入 token；托盘菜单提供「复制访问 Token」

## 5. 验收标准

- [ ] `scripts/build_desktop.py` 一条命令产出 `dist/TogoSpace_<version>_<arch>.dmg`，其中只有一个 `.app`
- [ ] 首次启动 8 秒内（Apple Silicon，冷启动）出现主窗口；启动画面覆盖全过程
- [ ] 退出应用后 `pgrep -f TogoSpace-backend` 为空；`kill -9` 主程序后 3 秒内后端自行退出
- [ ] 关闭窗口后应用不退出、托盘可见、可重新唤起
- [ ] 桌面模式下后端仅监听 `127.0.0.1`，局域网不可访问
- [ ] 桌面模式下所有 API 与 WebSocket 调用均通过鉴权，前端无需用户手输 token
- [ ] 老 `~/.togospace` 数据在新版中直接可用，数据库迁移在启动画面中有明确提示
- [ ] 公证后的包在干净机器上（无 Python 环境）可正常启动
- [ ] 浏览器访问同一后端依然可用（含 token 输入流程）
