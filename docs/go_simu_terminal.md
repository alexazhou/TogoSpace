# Go 版终端模拟器 (Go Simu Terminal)

[go_simu_terminal](https://github.com/alexazhou/go_simu_terminal) 是一个"无头"终端模拟器，能以无图形界面模式运行命令行程序，并将终端内容实时渲染为 **PNG** 或 **SVG**，内置 HTTP 控制接口，适合 AI Agent 操作 TUI。

`simu_terminal_go` 已安装到系统 PATH，可直接使用。

## 核心特性

- **实时渲染**：将终端内容渲染为矢量图 (SVG) 或位图 (PNG)
- **HTTP 控制**：内置 Web 服务，支持通过 API 发送输入（按键/文本）并获取截图
- **快照模式**：运行命令完成后自动捕获并保存终端屏幕
- **多语言支持**：内置 Menlo (ASCII) 和文泉驿微米黑 (CJK) 字体，完美支持中文显示
- **跨平台**：支持 Linux 和 macOS

## 核心原理

模拟器通过以下层级实现功能：

1. **PTY 层** (`creack/pty`): 创建伪终端并启动子进程，处理原始字节流的输入输出。
2. **仿真层** (`go-headless-term`): VT220 兼容的无头终端，解析 ANSI 转义序列，在内存中维护逻辑字符网格（Grid）。支持 CJK 宽字符、ANSI 16 色/256 色/TrueColor、光标与滚动区域管理。
3. **渲染层** (`render.go`): 将字符网格序列化为 SVG 或 PNG。宽字符占用 `2 * cellW` 空间，使用 `clip-path` 防止溢出。
4. **接口层** (`server.go`): 提供 HTTP API。

## 使用方法

### 服务模式（Interactive Mode）

启动交互式终端并开启 HTTP 服务：

```bash
simu_terminal_go --port 8889 --rows 36 --cols 140 -- .venv/bin/python tui/main.py --base-url http://127.0.0.1:8080
```

### 快照模式（Snapshot Mode）

运行命令并直接保存结果到文件：

```bash
# 保存为 PNG
simu_terminal_go --snapshot output.png --scale 2.0 --timeout 5 -- ls -alh

# 保存为 SVG
simu_terminal_go --snapshot output.svg --timeout 5 -- ls -alh
```

### 常用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--port` | HTTP 服务监听端口 | `8888` |
| `--cols` | 终端宽度（列数） | `140` |
| `--rows` | 终端高度（行数） | `36` |
| `--scale` | 渲染缩放比例（用于高清导出） | `1.0` |
| `--snapshot` | 快照模式，保存到指定文件（.png / .svg） | (空) |
| `--timeout` | 自动退出延时（秒，支持浮点数，0 不启用） | `0` |
| `--font-ascii` | 自定义 ASCII 字体路径 (.ttf) | (内置 Menlo) |
| `--font-cjk` | 自定义中文字体路径 (.ttf/.ttc) | (内置文泉驿) |

## API 参考

### `GET /screenshot`

获取当前终端画面的截图。

- 参数：`format` (png/svg，默认 svg)，`scale` (缩放比例)，`save` (保存到服务器本地路径)
- 示例：
  ```bash
  # PNG（推荐，可直接用 Read tool 读取图片）
  curl "http://localhost:8889/screenshot?format=png&scale=2" -o screenshot.png

  # SVG
  curl "http://localhost:8889/screenshot?format=svg" -o screenshot.svg
  ```

### `GET /export`

以文件下载形式导出当前终端画面。

- 参数：`format` (png/svg)，`scale` (缩放比例)，`filename` (文件名)
- 示例：
  ```bash
  curl "http://localhost:8889/export?format=png&scale=2" -o screenshot.png
  ```

### `POST /input`

向终端发送按键或文字。

- 请求体：
  ```json
  {
    "key": "tab",    // 特殊按键：up, down, left, right, enter, tab, esc, ctrl+a...ctrl+z
    "text": "hello"  // 发送原始字符串（与 key 二选一）
  }
  ```
- 示例：
  ```bash
  curl -X POST http://localhost:8889/input -H 'Content-Type: application/json' -d '{"key":"enter"}'
  curl -X POST http://localhost:8889/input -H 'Content-Type: application/json' -d '{"text":"ls -la\n"}'
  ```

## CJK 支持说明

通过 `go-headless-term` 的 `IsWide()` 和 `IsWideSpacer()` 特性实现正确的中文对齐：

- **IsWide**：检测到宽字符（如"中"）时，渲染宽度设为 `cellW * 2`
- **IsWideSpacer**：跳过宽字符后的占位单元格，避免重复渲染
- **对齐**：SVG 的 `textLength` 属性强制字符精确匹配单元格宽度
