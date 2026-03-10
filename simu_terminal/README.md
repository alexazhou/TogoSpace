# Python 版终端模拟器 (simu_terminal)

`simu_terminal/` 是一个常驻 HTTP 服务，将任意命令运行在 PTY 中，通过 HTTP 接口接受键盘输入和触发截图，用于在无显示器环境下自动化测试 TUI。

## 目录结构

```
simu_terminal/
├── main.py      # 入口，解析参数，启动服务
├── terminal.py  # TerminalProcess（PTY 管理 + pyte 渲染）
├── server.py    # aiohttp HTTP 服务
└── render.py    # 渲染函数（render_screen、resolve_color、is_cjk 等）
```

## HTTP 接口

| 方法 | 路径 | 请求体 | 响应 |
|------|------|--------|------|
| POST | `/input` | `{"text": "..."}` 或 `{"key": "up"}` | `{"ok": true}` |
| GET  | `/screenshot` | — | `image/png` |

**key 支持**：`up` `down` `left` `right` `enter` `tab` `esc` `ctrl+c` `ctrl+q` 等。

## 使用方法

**前置条件**：TUI 有单实例保护，须先停止已有 TUI 实例再启动模拟器。

```bash
# 1. 确保后端在运行
./scripts/start_backend.sh

# 2. 停止已有 TUI（如有）
./scripts/stop_tui.sh

# 3. 启动终端模拟器（-- 后为要运行的命令）
python -m simu_terminal.main --port 8888 -- .venv/bin/python tui/main.py --base-url http://127.0.0.1:8080 &

# 4. 等待 TUI 初始化（约 4-5 秒），截图确认初始状态
sleep 5 && curl http://localhost:8888/screenshot --output /tmp/t1.png

# 5. 发送键盘输入
curl -X POST http://localhost:8888/input -H 'Content-Type: application/json' -d '{"key":"tab"}'
curl -X POST http://localhost:8888/input -H 'Content-Type: application/json' -d '{"text":"hello
"}'

# 6. 再截图确认变化
curl http://localhost:8888/screenshot --output /tmp/t2.png
```

## 注意事项

- `simu_terminal` 完全独立，不依赖 `tui/`，可用于运行任意命令。
- 输出格式为 **PNG**。
- `--cols` / `--rows` 可调整终端尺寸（默认 140×36）。
- Textual 组件需要先获得焦点才能响应方向键，通常先发 `tab` 再发方向键。
