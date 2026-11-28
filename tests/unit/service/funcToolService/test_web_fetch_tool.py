"""`webfetch` 工具层护栏（#191）。

分两层：

1. **确定性用例**（默认跑）：HTML→Markdown 清洗、截断文案、URL 形态校验、
   SSRF 网段判定（含 IPv4-mapped 绕过）、编码探测、以及用**本机
   `ThreadingHTTPServer`** 覆盖的整条链路（404/500、重定向链、重定向超限、
   非 HTML、超大响应体、超时、SPA 空壳、真入口 `run_tool_call`）。
2. **真实网页用例**（`WEBFETCH_NETWORK_TESTS=1` 时才跑）：判据 3 要求「真实网页跑，
   不许只 mock」，但联网断言不能进默认全量回归（会因网络/对方改版而红）。默认跳过，
   实测读数写进 `shared/webfetch-tool/report.md`。

⚠️ 本机 `ThreadingHTTPServer` 监听 `127.0.0.1`，而 127.0.0.0/8 正是 SSRF 要拦的网段。
所以凡是要发起本地请求的用例，都用 `allow_local_requests` 把 `_assert_host_allowed`
包一层：**只对 127.0.0.1 / localhost 放行，其余地址仍走真实校验**——这样「重定向后
重新校验」这一条才能被真正验到（跳到 `10.0.0.1` 的用例依赖这一点）。
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import aiohttp
import pytest

from service import funcToolService
from service.funcToolService import webFetchTool
from service.funcToolService.core import get_func_tool, load_func_tools
from service.funcToolService.funcToolType import get_function_metadata
from service.roomService import ToolCallContext

# ---------------------------------------------------------------------------
# 夹具：本机 HTTP 服务器 + SSRF 放行包装
# ---------------------------------------------------------------------------

_GBK_TEXT = "这是用 GBK 编码的中文页面，用来验证编码探测没有退化成乱码。"
_LONG_SOURCE = "".join(f"<p>第{i}段：这是一段用于撑大页面的填充文本。</p>" for i in range(6000))

#: Markdown 直通判据的样本（#387）。刻意**不带首尾空白**：直通分支与 `text/plain` 共用
#: `strip()` 归一化，去掉首尾空白后 `strip()` 是恒等变换，"逐字符相同"才是字面成立的。
#: 内容覆盖 `#` 标题、`*`/`**` 强调、`<div>` 裸 HTML、有序/无序列表、代码围栏、引用块。
_MARKDOWN_SOURCE = (
    "# 一级标题\n"
    "\n"
    "正文含 **加粗**、*斜体* 与行内代码 `code`。\n"
    "\n"
    "1. 第一项\n"
    "2. 第二项\n"
    "\n"
    "- 无序项 A\n"
    "- 无序项 B\n"
    "\n"
    '<div class="note">HTML 片段原样保留</div>\n'
    "\n"
    "```python\n"
    "print('代码围栏')\n"
    "```\n"
    "\n"
    "> 引用块\n"
    "\n"
    "末尾一行没有换行符"
)


class _Handler(BaseHTTPRequestHandler):
    """按路径返回固定响应的最小服务器。"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静音，避免污染 pytest 输出
        pass

    def _send(self, status: int, content_type: str, body: bytes, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # 客户端在 `fetch_too_large` 时会主动中止读取，写侧报错属预期。
            pass

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定的方法名
        path = self.path.split("?")[0]

        if path == "/ok":
            html = (
                "<html><head><title>不该出现的标题</title>"
                "<style>body{color:red}</style></head><body>"
                "<nav>Navigation [index] [next]</nav>"
                "<h1>主标题</h1><h2>小节</h2>"
                "<table><tr><th>名称</th><th>值</th></tr>"
                "<tr><td>alpha</td><td>1</td></tr></table>"
                "<pre><code>print('hi')\n</code></pre>"
                "<script>console.log('不该出现')</script>"
                "<p>正文段落</p></body></html>"
            )
            self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
        elif path == "/gbk":
            self._send(200, "text/html; charset=gbk", f"<html><body><p>{_GBK_TEXT}</p></body></html>".encode("gbk"))
        elif path == "/gbk-nocharset":
            self._send(200, "text/html", f"<html><body><p>{_GBK_TEXT}</p></body></html>".encode("gbk"))
        elif path == "/plain":
            self._send(200, "text/plain; charset=utf-8", "纯文本内容 line1\nline2\n".encode("utf-8"))
        elif path == "/markdown":
            # 规范写法（RFC 7763）：jsDelivr 等按此标注 `.md`
            self._send(200, "text/markdown; charset=utf-8", _MARKDOWN_SOURCE.encode("utf-8"))
        elif path == "/x-markdown":
            # 非标准遗留写法：**当前不放行**（决策与依据见 #387 报告 §4）
            self._send(200, "text/x-markdown; charset=utf-8", _MARKDOWN_SOURCE.encode("utf-8"))
        elif path == "/text-csv":
            # 白名单逐项枚举的哨兵：绝不能被放宽成 `text/*`
            self._send(200, "text/csv; charset=utf-8", b"a,b\n1,2\n")
        elif path == "/404":
            self._send(404, "text/html; charset=utf-8", b"<html><body>not found</body></html>")
        elif path == "/500":
            self._send(500, "text/html; charset=utf-8", b"<html><body>boom</body></html>")
        elif path == "/redir1":
            self._send(302, "text/html", b"", {"Location": "/redir2"})
        elif path == "/redir2":
            self._send(302, "text/html", b"", {"Location": "/ok"})
        elif path == "/redir-loop":
            self._send(302, "text/html", b"", {"Location": "/redir-loop"})
        elif path == "/redir-private":
            # 跳到内网地址：模拟「重定向到 SSRF 目标」，必须被重新校验拦下。
            self._send(302, "text/html", b"", {"Location": "http://10.0.0.1:81/internal"})
        elif path == "/png":
            self._send(200, "image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        elif path == "/pdf":
            self._send(200, "application/pdf", b"%PDF-1.4\n" + b"\x00" * 64)
        elif path == "/big":
            self._send(200, "text/html; charset=utf-8", b"<html><body>" + b"x" * (6 * 1024 * 1024) + b"</body></html>")
        elif path == "/long":
            self._send(200, "text/html; charset=utf-8", f"<html><body>{_LONG_SOURCE}</body></html>".encode("utf-8"))
        elif path == "/spa":
            self._send(
                200,
                "text/html; charset=utf-8",
                b"<html><head><title>spa</title></head><body>"
                b"<div id='root'></div>"
                b"<script>document.getElementById('root').textContent='JS_ONLY_TEXT';</script>"
                b"</body></html>",
            )
        elif path == "/slow":
            time.sleep(5)
            try:
                self._send(200, "text/html; charset=utf-8", b"<html><body>late</body></html>")
            except OSError:
                pass
        else:
            self._send(404, "text/plain", b"unknown")


@pytest.fixture(scope="module")
def base_url():
    """模块级本机服务器：端口 0 由内核分配，避免与其他测试抢端口。"""

    class _Server(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            """静音：`fetch_too_large` / 超时用例会主动断开连接，服务器侧报
            `ConnectionResetError` 属预期，不该在测试输出里冒充失败。"""

    server = _Server(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def allow_local_requests(monkeypatch):
    """只对 127.0.0.1 / localhost 放行，其余地址仍走真实 SSRF 校验。"""
    original = webFetchTool._assert_host_allowed
    calls: list[str] = []

    async def wrapper(hostname: str, port: int):
        calls.append(hostname.lower())
        if hostname.lower() in {"127.0.0.1", "localhost"}:
            return None
        return await original(hostname, port)

    monkeypatch.setattr(webFetchTool, "_assert_host_allowed", wrapper)
    return calls


@pytest.fixture(autouse=True)
def _loaded_registry():
    """`webfetch` 要能从注册表里被取到（真入口用例依赖）。"""
    load_func_tools()
    yield


# ---------------------------------------------------------------------------
# 1. HTML → Markdown：一次性固定清洗 + 结构保真
# ---------------------------------------------------------------------------


def test_html_to_markdown_drops_scripts_styles_and_head():
    """`DROP_TAGS` 与 `<head>` 一律不入正文（标题/样式/脚本文本都不该出现）。"""
    html = (
        "<html><head><title>不该出现的标题</title><style>body{color:red}</style></head>"
        "<body><noscript>请开启 JS</noscript><iframe src='x'></iframe>"
        "<h1>主标题</h1><script>console.log('不该出现')</script><p>正文段落</p></body></html>"
    )
    markdown = webFetchTool.html_to_markdown(html)

    assert "主标题" in markdown
    assert "正文段落" in markdown
    for dropped in ("不该出现的标题", "color:red", "请开启 JS", "console.log", "x"):
        assert dropped not in markdown, f"被丢弃的内容仍出现在正文里: {dropped}"


def test_html_to_markdown_keeps_table_heading_and_code_fence():
    """判据 3「技术文档页结构保真」的最小版本：标题 / 表格 / 代码围栏三项都在。"""
    html = (
        "<html><body><h1>主标题</h1><h2>小节</h2>"
        "<table><tr><th>名称</th><th>值</th></tr><tr><td>alpha</td><td>1</td></tr></table>"
        "<pre><code>print('hi')\n</code></pre></body></html>"
    )
    markdown = webFetchTool.html_to_markdown(html)

    assert markdown.startswith("# 主标题"), markdown[:40]
    assert "## 小节" in markdown
    assert "| 名称 |" in markdown and "| alpha |" in markdown, "表格未被 markdownify 保真"
    assert "```" in markdown, "代码块未渲染成围栏"


# ---------------------------------------------------------------------------
# 2. 截断
# ---------------------------------------------------------------------------


def test_truncate_content_keeps_short_text_unchanged():
    text = "短文本" * 10
    assert webFetchTool.truncate_content(text) == text


def test_truncate_content_marks_original_length():
    """判据 5：长度 ≤ 上限 + 提示语，且提示里写明原文长度。"""
    original = "字" * (webFetchTool.MAX_CONTENT_CHARS + 4321)
    truncated = webFetchTool.truncate_content(original)

    assert truncated.startswith(original[: webFetchTool.MAX_CONTENT_CHARS])
    assert "已截断" in truncated
    assert str(len(original)) in truncated
    assert len(truncated) <= webFetchTool.MAX_CONTENT_CHARS + 100


# ---------------------------------------------------------------------------
# 3. URL 形态与 SSRF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "gopher://example.com/",
        "example.com/no-scheme",
        "http://",
        "",
        "   ",
        "http://example.com:99999/x",
    ],
)
def test_split_url_rejects_bad_forms(url):
    assert webFetchTool._split_url(url) is None


@pytest.mark.parametrize("url", ["http://example.com/x", "https://example.com:8443/x?q=1"])
def test_split_url_accepts_http_forms(url):
    assert webFetchTool._split_url(url) is not None


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.255.255.254",
        "10.1.2.3",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "fe80::1",
        "::ffff:127.0.0.1",  # IPv4-mapped，必须先还原再判
    ],
)
def test_ip_is_blocked_for_internal_networks(ip):
    import ipaddress

    assert webFetchTool._ip_is_blocked(ipaddress.ip_address(ip)) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "172.32.0.1", "2606:4700:4700::1111"])
def test_ip_is_blocked_allows_public_addresses(ip):
    import ipaddress

    assert webFetchTool._ip_is_blocked(ipaddress.ip_address(ip)) is False


async def test_assert_host_allowed_blocks_literal_private_ip():
    result = await webFetchTool._assert_host_allowed("127.0.0.1", 80)
    assert result is not None and result["error_code"] == "fetch_blocked_host"


async def test_assert_host_allowed_blocks_localhost_name():
    result = await webFetchTool._assert_host_allowed("localhost", 80)
    assert result is not None and result["error_code"] == "fetch_blocked_host"


async def test_assert_host_allowed_blocks_domain_resolving_to_private_ip(monkeypatch):
    """先解析再判 IP：域名本身干净，但 A 记录指向内网，必须拦。"""

    def fake_resolve(host, port):
        import ipaddress

        return [ipaddress.ip_address("10.0.0.7")]

    monkeypatch.setattr(webFetchTool, "_resolve_ips_sync", fake_resolve)
    result = await webFetchTool._assert_host_allowed("innocent.example.com", 80)
    assert result is not None and result["error_code"] == "fetch_blocked_host"


async def test_assert_host_allowed_accepts_public_domain(monkeypatch):
    def fake_resolve(host, port):
        import ipaddress

        return [ipaddress.ip_address("93.184.216.34")]

    monkeypatch.setattr(webFetchTool, "_resolve_ips_sync", fake_resolve)
    assert await webFetchTool._assert_host_allowed("example.com", 80) is None


# ---------------------------------------------------------------------------
# 4. 编码探测
# ---------------------------------------------------------------------------


def test_decode_body_prefers_content_type_charset():
    body = _GBK_TEXT.encode("gbk")
    assert webFetchTool._decode_body(body, "gbk") == _GBK_TEXT


def test_decode_body_falls_back_to_detection_when_charset_missing():
    """没有 charset 时靠 charset-normalizer 认出 GBK；退化成 utf-8 会出乱码。"""
    body = _GBK_TEXT.encode("gbk")
    decoded = webFetchTool._decode_body(body, None)
    assert decoded == _GBK_TEXT
    assert "\ufffd" not in decoded


# ---------------------------------------------------------------------------
# 5. 整条链路（本机服务器）
# ---------------------------------------------------------------------------


async def test_fetch_markdown_returns_contract_fields(base_url, allow_local_requests):
    result = await webFetchTool.fetch_markdown(f"{base_url}/ok")

    assert result["success"] is True
    assert set(result) == {"success", "message", "url", "content_type", "original_chars"}
    assert result["content_type"] == "text/html"
    assert result["url"] == f"{base_url}/ok"
    assert result["original_chars"] == len(result["message"])
    assert "主标题" in result["message"]
    # 不执行 JS：脚本内容不出现
    assert "不该出现" not in result["message"]


async def test_fetch_markdown_decodes_gbk_with_charset(base_url, allow_local_requests):
    result = await webFetchTool.fetch_markdown(f"{base_url}/gbk")
    assert result["success"] is True and _GBK_TEXT in result["message"]


async def test_fetch_markdown_decodes_gbk_without_charset(base_url, allow_local_requests):
    result = await webFetchTool.fetch_markdown(f"{base_url}/gbk-nocharset")
    assert result["success"] is True and _GBK_TEXT in result["message"]


async def test_fetch_markdown_passes_text_plain_through(base_url, allow_local_requests):
    result = await webFetchTool.fetch_markdown(f"{base_url}/plain")
    assert result["success"] is True
    assert result["content_type"] == "text/plain"
    assert result["message"].startswith("纯文本内容")


# ---------------------------------------------------------------------------
# 2b. `text/markdown` 直通（#387）
# ---------------------------------------------------------------------------


async def test_fetch_markdown_passes_text_markdown_through_char_exact(base_url, allow_local_requests):
    """判据 1：`text/markdown` 必须直通——返回正文与源文件**逐字符相同**。

    失效形态：把 Markdown 丢给 `html_to_markdown`。那样 `<div>` 会被当标签丢掉、
    `**加粗**` 变成 `<strong>` 再转回来、`#` 变标题层级——内容看起来"还行"，但原文已经被改写。
    """
    result = await webFetchTool.fetch_markdown(f"{base_url}/markdown")

    assert result["success"] is True, result
    assert result["content_type"] == "text/markdown"
    assert result["message"] == _MARKDOWN_SOURCE, "Markdown 正文被改写（多半是走了 HTML 转换）"
    assert result["original_chars"] == len(_MARKDOWN_SOURCE)
    for marker in ("# 一级标题", "**加粗**", "*斜体*", '<div class="note">', "1. 第一项", "```python", "> 引用块"):
        assert marker in result["message"], f"Markdown 语法被破坏: {marker}"


async def test_fetch_markdown_markdown_never_calls_html_converter(base_url, allow_local_requests, monkeypatch):
    """比"正文相同"更硬：`text/markdown` 分支**不得调用** `html_to_markdown`。

    "正文相同"是结果判据，若哪天转换器恰好对某类样本幂等，结果判据会假绿；这里直接
    钉住"没调用"这件事。
    """
    calls: list[str] = []

    def _forbidden(text: str) -> str:
        calls.append(text)
        raise AssertionError("text/markdown 不得进 HTML 转换")

    monkeypatch.setattr(webFetchTool, "html_to_markdown", _forbidden)

    result = await webFetchTool.fetch_markdown(f"{base_url}/markdown")

    assert calls == [], "Markdown 走了 HTML 转换分支"
    assert result["success"] is True
    assert result["message"] == _MARKDOWN_SOURCE


async def test_fetch_markdown_rejects_legacy_x_markdown(base_url, allow_local_requests):
    """决策记录：`text/x-markdown`（非标准遗留写法）**不放行**，依据见 #387 报告 §4。"""
    result = await webFetchTool.fetch_markdown(f"{base_url}/x-markdown")

    assert result["success"] is False
    assert result["error_code"] == "fetch_unsupported_content_type"
    assert "text/x-markdown" in result["message"]


async def test_fetch_markdown_rejects_other_text_subtypes(base_url, allow_local_requests):
    """白名单是**逐项枚举**，不是 `text/*`：`text/csv` 仍必须被拒。"""
    result = await webFetchTool.fetch_markdown(f"{base_url}/text-csv")

    assert result["success"] is False
    assert result["error_code"] == "fetch_unsupported_content_type"


async def test_fetch_markdown_http_html_still_converted(base_url, allow_local_requests):
    """判据 2：`text/html` 行为不变——仍进 HTML 转换（`<script>` 丢弃、标题保留）。"""
    result = await webFetchTool.fetch_markdown(f"{base_url}/ok")

    assert result["success"] is True
    assert result["content_type"] == "text/html"
    assert "主标题" in result["message"]
    assert "不该出现" not in result["message"], "<script> 内容没被清掉，HTML 分支行为变了"
    assert "<h1>" not in result["message"], "HTML 未被转成 Markdown"


@pytest.mark.parametrize("status_path", ["/404", "/500"])
async def test_fetch_markdown_maps_http_errors_to_structured_result(base_url, allow_local_requests, status_path):
    """404 / 500 必须是结构化失败体，不抛裸异常。"""
    result = await webFetchTool.fetch_markdown(f"{base_url}{status_path}")
    assert result["success"] is False
    assert result["error_code"] == "fetch_http_error"
    assert status_path.lstrip("/") in result["message"]


async def test_fetch_markdown_follows_redirect_chain(base_url, allow_local_requests):
    result = await webFetchTool.fetch_markdown(f"{base_url}/redir1")
    assert result["success"] is True
    assert result["url"] == f"{base_url}/ok", "最终 URL 应是重定向终点"
    assert "主标题" in result["message"]


async def test_fetch_markdown_stops_after_max_redirects(base_url, allow_local_requests):
    result = await webFetchTool.fetch_markdown(f"{base_url}/redir-loop")
    assert result["success"] is False
    assert result["error_code"] == "fetch_http_error"
    assert "重定向" in result["message"]


async def test_fetch_markdown_revalidates_redirect_target(base_url, allow_local_requests):
    """跳到内网地址的重定向必须在第二跳被拦（每跳都重新校验）。"""
    result = await webFetchTool.fetch_markdown(f"{base_url}/redir-private")

    assert result["success"] is False
    assert result["error_code"] == "fetch_blocked_host"
    assert allow_local_requests[:2] == ["127.0.0.1", "10.0.0.1"], allow_local_requests
    assert len(allow_local_requests) == 2, "被拦后不应继续请求"


@pytest.mark.parametrize("path", ["/png", "/pdf"])
async def test_fetch_markdown_rejects_non_text_content_types(base_url, allow_local_requests, path):
    result = await webFetchTool.fetch_markdown(f"{base_url}{path}")
    assert result["success"] is False
    assert result["error_code"] == "fetch_unsupported_content_type"


async def test_fetch_markdown_rejects_oversized_body(base_url, allow_local_requests):
    result = await webFetchTool.fetch_markdown(f"{base_url}/big")
    assert result["success"] is False
    assert result["error_code"] == "fetch_too_large"


async def test_fetch_markdown_truncates_long_page(base_url, allow_local_requests):
    result = await webFetchTool.fetch_markdown(f"{base_url}/long")
    assert result["success"] is True
    assert result["original_chars"] > webFetchTool.MAX_CONTENT_CHARS
    assert "已截断" in result["message"]
    assert len(result["message"]) <= webFetchTool.MAX_CONTENT_CHARS + 100


async def test_fetch_markdown_reports_empty_spa_shell(base_url, allow_local_requests):
    """不执行 JS：只有 JS 才能填充的页面 ⇒ 空内容 + 明确提示。"""
    result = await webFetchTool.fetch_markdown(f"{base_url}/spa")
    assert result["success"] is False
    assert result["error_code"] == "fetch_empty_content"
    assert "不执行 JS" in result["message"]


async def test_fetch_markdown_timeout_is_bounded(base_url, allow_local_requests, monkeypatch):
    """判据 4：故意慢的地址 ⇒ 在设定值 + 容差内返回 `fetch_timeout`。"""
    monkeypatch.setattr(webFetchTool, "TOTAL_TIMEOUT_SECONDS", 1)
    started = time.monotonic()
    result = await webFetchTool.fetch_markdown(f"{base_url}/slow")
    elapsed = time.monotonic() - started

    assert result["success"] is False
    assert result["error_code"] == "fetch_timeout"
    assert elapsed <= 3.0, f"超时未在上限附近生效，实际耗时 {elapsed:.2f}s"


async def test_fetch_markdown_blocks_private_host_before_any_request():
    """内网地址不必真的发请求，直接返回 `fetch_blocked_host`（不依赖本机服务器）。"""
    started = time.monotonic()
    result = await webFetchTool.fetch_markdown("http://127.0.0.1:1/x")
    elapsed = time.monotonic() - started

    assert result["success"] is False
    assert result["error_code"] == "fetch_blocked_host"
    assert elapsed < 1.0


async def test_fetch_markdown_rejects_invalid_url():
    result = await webFetchTool.fetch_markdown("ftp://example.com/x")
    assert result["success"] is False
    assert result["error_code"] == "fetch_invalid_url"


# ---------------------------------------------------------------------------
# 6. 真入口：解析 → 注册表 → handler → HTTP → 转换 → 返回
# ---------------------------------------------------------------------------


def _webfetch_description() -> str:
    tool = get_func_tool("webfetch")
    assert tool is not None, "webfetch 未注册进 load_func_tools()"
    return get_function_metadata("webfetch", tool.callable)["description"]


def test_webfetch_is_registered():
    tool = get_func_tool("webfetch")
    assert tool is not None
    metadata = get_function_metadata("webfetch", tool.callable)
    assert metadata["parameters"]["required"] == ["url"]


def test_tool_description_states_no_js_limit():
    """判据 2 的可判断部分：模型看得到的工具描述必须写明不执行 JS。"""
    description = _webfetch_description()
    for phrase in ("不执行 JavaScript", "100000"):
        assert phrase in description, f"工具描述缺少关键限制: {phrase}"


def test_webfetch_description_is_compact_and_keeps_deciding_info():
    """#388：description 压到 ≤200 字符，但决定模型行为的 5 条信息必须逐条在场。

    两条判据必须成对：只钉体积，把文案删空也是绿；只钉必留项，重新写回 700 字也是绿。

    体积只钉**模型可见口径** `get_function_metadata()["description"]`（≤200）：
    这才是模型收到的字。`callable.__doc__`（含 `Args:` 段与缩进）与它不是同一个量，
    `Args:` 段进 schema 的结构化参数说明、不在 description 里，**不作判据**
    （主管 12:06 在 #388 上定的口径；报数可以两个都给）。
    改前：模型可见 667（`294e55a0`）/ 764（本单起点 `6c14eb96`），含 Args 的原文 773 / 878。
    """
    description = _webfetch_description()
    assert len(description) <= 200, (
        f"webfetch description 已膨胀到 {len(description)} 字符（上限 200）：\n{description}"
    )

    for phrase, why in (
        ("Markdown", "①用途：返回的是 Markdown 正文"),
        ("用于深入阅读", "①用途：搜完该读的行为提示"),
        ("不执行 JavaScript", "②不跑页面脚本"),
        ("反复重试同一地址", "②SPA 空壳时别反复重试同一地址"),
        ("PDF / Office / 图片", "③不解析 PDF / Office / 图片"),
        ("100000", "④正文截断阈值"),
        ("原文长度", "④截断后末尾注明原文长度"),
        ("fetch_blocked_host", "⑤内网 / 本机地址被拒"),
        ("导航/侧栏残留", "⑥返回可能含导航 / 侧栏残留"),
        ("引用时不要当作正文内容", "⑥行为指引：别把残留当正文引用"),
    ):
        assert phrase in description, (
            f"必留信息缺失（{why}）：{phrase!r}\n当前 description=\n{description}"
        )

    # ⑥ 的判据是行为指引「别当正文引用」，不是语气建议「请忽略」（#389 主管裁定）：
    # 「请忽略」无法检验，写回它等于把这条判据变回橡皮图章。
    assert "请忽略" not in description, "⑥ 退化回无法检验的语气建议「请忽略」"

    # ⑤ 的另一半在参数说明里（`url` 必须 http/https 绝对地址），本单不动它，但钉住。
    tool = get_func_tool("webfetch")
    assert tool is not None
    url_desc = get_function_metadata("webfetch", tool.callable)["parameters"]["properties"]["url"][
        "description"
    ]
    for phrase in ("http", "https", "绝对地址"):
        assert phrase in url_desc, f"`url` 参数说明缺少：{phrase!r}，当前={url_desc!r}"


async def test_run_tool_call_entry_returns_markdown(base_url, allow_local_requests):
    """判据 1：从统一入口 `run_tool_call` 走到底。"""
    context = ToolCallContext(agent_id=7, team_id=2, tool_name="webfetch")
    result = await funcToolService.run_tool_call(f'{{"url": "{base_url}/ok"}}', context)

    assert result["success"] is True
    assert "主标题" in result["message"]
    assert result["content_type"] == "text/html"


async def test_run_tool_call_entry_surfaces_error_code(base_url, allow_local_requests):
    context = ToolCallContext(agent_id=7, team_id=2, tool_name="webfetch")
    result = await funcToolService.run_tool_call(f'{{"url": "{base_url}/404"}}', context)

    assert result["success"] is False
    assert result["error_code"] == "fetch_http_error"


# ---------------------------------------------------------------------------
# 7. 真实网页（默认跳过；`WEBFETCH_NETWORK_TESTS=1` 时跑）
# ---------------------------------------------------------------------------

_network = pytest.mark.skipif(
    os.environ.get("WEBFETCH_NETWORK_TESTS") != "1",
    reason="联网用例默认跳过：设 WEBFETCH_NETWORK_TESTS=1 才跑（判据 3 用）",
)


@_network
async def test_real_docs_page_keeps_structure():
    """经典用例集：技术文档页，标题 / 代码围栏至少一项保真。"""
    result = await webFetchTool.fetch_markdown("https://docs.python.org/3/library/asyncio-task.html")

    assert result["success"] is True
    assert result["original_chars"] > 10_000
    assert result["message"].count("```") >= 2, "代码围栏未保真"
    assert "# " in result["message"], "标题未保真"


@_network
async def test_real_article_page():
    result = await webFetchTool.fetch_markdown("https://peps.python.org/pep-0008/")
    assert result["success"] is True
    assert "PEP 8" in result["message"]


@_network
async def test_real_large_page_is_truncated_with_original_length():
    """判据 3「超大页面」的真实版本：stdtypes 原文 > 10 万字符 ⇒ 截断 + 写明原文长度。"""
    result = await webFetchTool.fetch_markdown("https://docs.python.org/3/library/stdtypes.html")

    assert result["success"] is True
    assert result["original_chars"] > webFetchTool.MAX_CONTENT_CHARS
    assert "已截断" in result["message"]
    assert str(result["original_chars"]) in result["message"]
    assert len(result["message"]) <= webFetchTool.MAX_CONTENT_CHARS + 100


@_network
async def test_real_redirect_chain_lands_on_final_url():
    """真实重定向链：`/3/library/stdtypes.html` ⇒ `/3/builtins/stdtypes.html`（实测）。"""
    result = await webFetchTool.fetch_markdown("https://docs.python.org/3/library/stdtypes.html")

    assert result["success"] is True
    assert result["url"] != "https://docs.python.org/3/library/stdtypes.html"
    assert result["url"].endswith("/3/builtins/stdtypes.html"), result["url"]


@_network
async def test_real_404_is_structured_error():
    result = await webFetchTool.fetch_markdown("https://docs.python.org/3/library/no-such-page-xyz.html")
    assert result["success"] is False
    assert result["error_code"] == "fetch_http_error"


@_network
async def test_real_non_html_is_unsupported():
    result = await webFetchTool.fetch_markdown("https://httpbin.org/image/png")
    assert result["success"] is False
    assert result["error_code"] == "fetch_unsupported_content_type"


@_network
async def test_real_jsdelivr_markdown_matches_source_bytes_char_exact():
    """#387 判据 1 的真实版本：jsDelivr 的 `.md` 标 `text/markdown`。

    逐字符比对的对象不是"我记得的内容"，而是**同一 URL 的原始字节**（另开一个
    不经本工具的 aiohttp 请求取回、解码、`strip()` 后比）：这样"没被 HTML 转换改写"
    是被源文件本身证明的，而不是靠断言里手写的片段。
    """
    url = "https://cdn.jsdelivr.net/gh/microsoft/vscode@main/README.md"

    result = await webFetchTool.fetch_markdown(url)

    async with aiohttp.ClientSession(trust_env=False) as session:
        async with session.get(url, headers={"User-Agent": webFetchTool.USER_AGENT}) as response:
            assert response.status == 200
            source = (await response.read()).decode("utf-8")

    assert result["success"] is True, result
    assert result["content_type"] == "text/markdown"
    assert result["message"] == source.strip(), "Markdown 正文与源文件不是逐字符相同"
    assert "#" in result["message"], "Markdown 标题标记丢失"


@_network
async def test_real_github_raw_markdown_still_text_plain():
    """对照：同一份文件的 GitHub raw 版本标 `text/plain`，历史上能读——现在两条都通。"""
    result = await webFetchTool.fetch_markdown(
        "https://raw.githubusercontent.com/microsoft/vscode/main/README.md"
    )

    assert result["success"] is True, result
    assert result["content_type"] == "text/plain"
    assert "#" in result["message"]


@_network
async def test_real_spa_shell_is_near_empty():
    """不执行 JS 的实测后果：纯 CSR 首页只能拿到空壳。"""
    result = await webFetchTool.fetch_markdown("https://vercel.com/")
    assert result["success"] is True, result
    digest = hashlib.md5(result["message"].encode("utf-8")).hexdigest()
    assert result["original_chars"] < 100_000, digest
