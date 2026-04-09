# 桌面打包发布方案设计

## 目标

通过 PyInstaller 将后端服务打包为桌面应用，启动后在系统托盘（菜单栏）常驻，提供：

- 查看版本
- 打开 Web 界面（浏览器）
- 服务状态展示
- 退出

## 两种启动模式

| 入口 | 模式 | 说明 |
|------|------|------|
| `src/backend_main.py` | 纯后端，无 GUI | 现有行为完全不变，命令行直接运行 |
| `src/app_entry.py` | 后端 + 托盘 GUI | PyInstaller 打包入口，启动托盘并在子线程内运行后端 |

`app_entry.py` 直接调用 `backend_main.main(config_dir, port)`，后端代码**零改动**。

---

## 平台支持

目标：**跨平台**（macOS / Windows / Linux）

### 托盘库选型：pystray

| 平台 | 底层依赖 | 状态 |
|------|----------|------|
| macOS | `pyobjc-framework-Cocoa` | 可用，体验接近原生 |
| Windows | `pywin32` 或内置 WinAPI | 最完善 |
| Linux | `python3-xlib` + X11，或 `ayatana-appindicator` | 依赖桌面环境，纯 Wayland 有坑 |

### 依赖清单

```
pystray
Pillow                       # 图标处理（必须）
pyobjc-framework-Cocoa       # macOS
pywin32                      # Windows（pystray 会自动使用 WinAPI，可选显式安装）
```

Linux 额外：
```
python3-xlib    # X11 环境
# 或
ayatana-appindicator  # Ubuntu / GNOME
```

---

## 架构设计

### 进程模型：同进程 + 多线程

**选定方案**：单一进程，主线程运行托盘，子线程运行后端服务。

```
主进程
├── 主线程：pystray icon.run()
│           菜单事件回调（直接调用后端方法，无需 IPC）
└── 子线程：tornado IOLoop（后端服务）
            asyncio event loop 在此线程内运行
```

优点：
- 单一可执行文件，打包简单
- 菜单回调可直接调用后端的 Python 方法，无需进程间通信
- 未来扩展菜单功能（如重置房间、查看状态）只需暴露接口即可

### tornado 在子线程中启动

tornado 的 `IOLoop` 不能跨线程直接调用，菜单回调若需触发异步操作，通过
`asyncio.run_coroutine_threadsafe(coro, loop)` 投递到后端线程的 event loop：

```python
# 菜单回调示例
def on_open_browser(icon, item):
    webbrowser.open("http://localhost:8080")

def on_restart_server(icon, item):
    # 跨线程调用后端异步方法
    asyncio.run_coroutine_threadsafe(backend.restart(), backend_loop)
```

后端线程持有 event loop 引用，启动时保存供主线程使用：

```python
backend_loop: asyncio.AbstractEventLoop = None

def run_backend():
    global backend_loop
    backend_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(backend_loop)
    # 启动 tornado ...
    backend_loop.run_forever()

threading.Thread(target=run_backend, daemon=True).start()
```

---

## 菜单结构设计

```
[图标] AgentTeam
  ├── 状态: 运行中 ●       （灰色，不可点击，动态更新）
  ├── 打开 Web 界面
  ├── ─────────────
  ├── 版本: v1.0.0          （灰色，不可点击）
  └── 退出
```

可扩展项（后续）：
- 启动 / 停止服务
- 查看日志目录

### 首次启动行为

若 `~/.agent_team/setting.json` 不存在：

1. 自动创建默认配置文件（`SettingConfig` 默认值序列化写入）
2. 托盘弹出系统通知，提示用户去编辑该文件配置 LLM 服务

**需要改动**：`configUtil._load_setting()` 当前文件不存在时只返回默认值，不写文件，需补充写文件逻辑。

---

## PyInstaller 打包

### 各平台需独立打包

PyInstaller 不支持交叉编译，需在对应平台上执行打包：

| 产物 | 平台 |
|------|------|
| `.app` | macOS |
| `.exe` | Windows |
| ELF 可执行 | Linux |

### 打包命令（草稿）

```bash
pyinstaller --windowed --onedir \
  --add-data "preset:preset" \
  --add-data "src/prompts:src/prompts" \
  --add-data "assets/frontend:assets/frontend" \
  --icon assets/icon.icns \
  src/app_entry.py
```

### 目录分工

| 目录 | 位置 | 说明 |
|------|------|------|
| `preset/` | 内嵌 bundle（只读） | role_templates / teams 预置内容，随版本发布 |
| `src/prompts/` | 内嵌 bundle（只读） | 系统 prompt 文件，随版本发布 |
| `assets/frontend/` | 内嵌 bundle（只读） | Web 前端构建产物，由后端托管；打包前需手动从 `frontend/dist` 复制 |
| `~/.agent_team/` | 用户主目录（可写） | `setting.json` 等用户配置，已在用户目录，无需处理 |

### 主要注意事项

1. **隐式依赖**：tornado、pydantic 等需在 `.spec` 文件中声明 `hiddenimports`
2. **`--windowed`**：不弹终端窗口（macOS/Windows）
3. **图标格式**：macOS 需 `.icns`，Windows 需 `.ico`，pystray 运行时使用 `PIL.Image`
4. **路径解析**：打包后 `__file__` 指向 bundle 内路径，`configUtil._resolve_preset_dir()` 依赖 `__file__` 相对路径，打包后需验证是否正确解析（可能需要改用 `sys._MEIPASS`）
5. **macOS 必要初始化**（已在 `app_entry.py` 实现）：
   - `NSApplication.setActivationPolicy_(NSApplicationActivationPolicyAccessory)`：无 Dock 图标，仅菜单栏常驻
   - `image.setTemplate_(True)`：图标标记为 template image，自动适配深/浅色菜单栏

### 发布流程（CI）

如需自动化发布，可用 GitHub Actions 在三个平台的 runner 上分别打包，产物上传到 Release。

---

## 待讨论问题

- [x] 后端运行方式：同进程多线程，主线程托盘 + 子线程 tornado
- [x] config 目录：用户配置已在 `~/.agent_team/`，天然可写，无需处理
- [x] 端口冲突：报错提示用户去修改 `~/.agent_team/setting.json` 中的端口配置
- [x] 图标资源：临时生成占位图，存放于 `assets/`
- [x] 版本号来源：`src/version.py` 中的 `__version__`
- [x] Web 前端：由后端托管静态文件，构建产物放 `assets/frontend/`，打包时内嵌
- [x] `app_entry.py` 位置：放在 `src/` 下
