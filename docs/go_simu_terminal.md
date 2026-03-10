# Go 版终端模拟器 (Go Simu Terminal)

Go 版终端模拟器是一个轻量级的、无外部依赖的单二进制工具，旨在为 AI Agent 提供 TUI 观测和交互能力。

## 1. 核心原理

模拟器通过以下层级实现功能：

1.  **PTY 层** (`creack/pty`): 创建伪终端并启动子进程。它负责处理原始字节流的输入输出。
2.  **仿真层** (`go-headless-term`): 一个 VT220 兼容的无头终端。它解析 PTY 输出的 ANSI 转义序列，并在内存中维护一个逻辑字符网格（Grid）。该层支持：
    *   **CJK 宽字符**: 自动识别全角字符并分配 2 个单元格宽度。
    *   **颜色系统**: 支持 ANSI 16 色、256 色及 TrueColor。
    *   **状态管理**: 管理光标位置、滚动区域和字符属性（粗体、下划线等）。
3.  **渲染层** (`render.go`): 将内存中的字符网格序列化为 SVG 字符串。
    *   每个单元格根据颜色生成 `<rect>`（背景）和 `<text>`（前景）。
    *   宽字符占用 `2 * cellW` 的空间。
    *   使用 `clip-path` 确保字符不会溢出其所在的行。
4.  **接口层** (`server.go`): 提供 HTTP API。

## 2. 依赖项

| 包名 | 用途 |
| :--- | :--- |
| `github.com/creack/pty` | PTY 进程创建与 TTY 窗口大小管理 |
| `github.com/danielgatis/go-headless-term` | 终端仿真、状态机、CJK 宽度计算 |
| `net/http` | 标准库，提供 API 访问 |

## 3. 使用方法

### 构建

```bash
cd go_simu_terminal
go mod tidy
go build -o simu_terminal_go .
```

### 运行

启动模拟器时，在 `--` 之后指定要运行的命令：

```bash
# 示例：运行 Python TUI
./go_simu_terminal/simu_terminal_go --port 8889 -- .venv/bin/python tui/main.py --base-url http://127.0.0.1:8080
```

### 常用参数

*   `--port`: HTTP 服务监听端口（默认 8888）。
*   `--cols`: 终端宽度，以列为单位（默认 140）。
*   `--rows`: 终端高度，以行为单位（默认 36）。

## 4. API 参考

### `GET /screenshot`
获取当前终端画面的 SVG 截图。

**响应**: `image/svg+xml`

### `POST /input`
向终端发送按键或文字。

**请求体**:
```json
{
  "key": "tab",   // 可选值：up, down, left, right, enter, tab, esc, ctrl+a...ctrl+z
  "text": "hello" // 发送原始字符串
}
```

## 5. CJK 支持说明

旧版本 (`vt10x`) 无法正确处理全角字符，导致中文对齐错位。
当前版本通过 `go-headless-term` 的 `IsWide()` 和 `IsWideSpacer()` 特性解决了此问题：
*   **IsWide**: 当检测到宽字符（如“中”）时，渲染器将其宽度设为 `cellW * 2`。
*   **IsWideSpacer**: 跳过宽字符后的占位单元格，避免重复渲染。
*   **对齐**: SVG 的 `textLength` 属性强制字符精确匹配单元格宽度，确保视觉对齐。

## 6. 测试与验证

获取截图并查看效果：
```bash
curl http://localhost:8889/screenshot -o snapshot.svg && open snapshot.svg
```
验证颜色、粗体、中文字符位置是否与真实终端一致。
