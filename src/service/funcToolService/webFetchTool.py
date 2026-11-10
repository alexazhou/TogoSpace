"""`webfetch` 的取数与转换实现：HTTP 取 HTML → 转 Markdown，**不执行 JS**。

设计边界（#191 定版，不要在这里加可配置项）：

1. **单页读取**：一次请求 + 至多 5 次重定向，不做爬取、不遍历站点、不遵守
   robots.txt（理由：单页读取等价于用户自己打开这个链接，不构成站点遍历）。
2. **不执行 JavaScript**：只用 `beautifulsoup4` 解析服务端返回的 HTML，没有
   浏览器、没有 Playwright/Selenium。⇒ SPA / CSR 站点只能拿到空壳，这条限制
   已写进工具描述，由模型自己判断要不要换方法。
3. **清洗只做一次性固定小清单**：`DROP_TAGS` + `<head>` 整块不入正文。
   不做基于 id/class 的站点适配规则、不做站点白/黑名单、不设「正文是否够干净」
   的阈值、不做重试/回退分支 —— 单次转换、直出结果。
   ⇒ **接受残留导航噪声**（`Navigation`、`[index]/[next]/[previous]`、breadcrumb），
   这是有意的取舍：为去噪加复杂度不划算。
4. **安全兜底**（十几行代码，不算复杂度膨胀）：拦内网/回环/链路本地地址，
   **先解析域名再判 IP**（防 DNS 绕过），**每次重定向后重新校验目标**。

返回结构沿用项目既有口径：`{success, message, error_code?}`，成功时 `message`
是 Markdown 正文（或 `text/plain` 原文）。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
import certifi
from bs4 import BeautifulSoup
from markdownify import markdownify as _to_markdown

# ── 定版常量（#191 §3，不要做成配置项） ────────────────────────────────────

#: 输出字符上限：超出即截断，并在正文末尾写明原文长度。
MAX_CONTENT_CHARS = 100_000
#: HTML 响应体字节上限（5MB）：超出返回 `fetch_too_large`。
MAX_BODY_BYTES = 5 * 1024 * 1024
#: 重定向上限。
MAX_REDIRECTS = 5
#: 总超时 / 连接超时（秒）。
TOTAL_TIMEOUT_SECONDS = 30
CONNECT_TIMEOUT_SECONDS = 10

#: 真实浏览器 UA：不少文档站对默认 `aiohttp` UA 直接 403。
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

#: 允许处理的 Content-Type（小写、已去掉 charset 部分）。
ALLOWED_CONTENT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")

#: 一次性固定清洗清单：这些标签连同内容一起丢掉（`decompose` 而不是 `extract`，
#: 因为它们的文本对 Agent 无意义 —— 脚本源码、样式表、`<noscript>` 里的回退文案）。
DROP_TAGS = ("script", "style", "noscript", "iframe", "svg", "canvas", "template")

#: 内网 / 回环 / 链路本地 / 保留地址。`0.0.0.0` 与 `::` 单独列出：Linux 上访问
#: 它们等价于访问本机。
BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/32",
        "10.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)

#: 明确的本地主机名（`getaddrinfo("localhost")` 在部分环境解析到 `::1`，但也可能
#: 被 hosts 文件改写；直接按名字先拦一道）。
BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost"})


def _error(error_code: str, message: str) -> dict[str, Any]:
    """统一失败返回体（`{success, message, error_code}`）。"""
    return {"success": False, "message": message, "error_code": error_code}


def _ssl_context() -> ssl.SSLContext:
    """用 `certifi` 的 CA 包建上下文。

    **为什么必须显式给**：本机 / 部分打包环境的系统信任库缺中间证书，默认上下文下
    访问 `docs.python.org` 会直接 `CERTIFICATE_VERIFY_FAILED`。项目里
    `deepseekService` / `xiaomiMimoService` 已经是同一写法（`certifi.where()`），
    这里保持一致，不引入新口径。用 `certifi` 而不是关校验 —— 关校验会让抓取变成
    MITM 的靶子，而抓来的内容会直接进模型上下文。
    """
    return ssl.create_default_context(cafile=certifi.where())


# ── URL 与 SSRF 校验 ─────────────────────────────────────────────────────


def _split_url(url: str):
    """解析并校验 URL 的基本形态；不合法返回 `None`。

    只放行 `http` / `https`（`file://`、`ftp://`、`gopher://` 一律拒），要求有
    hostname。
    """
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    if not parts.hostname:
        return None
    # `urlsplit` 不在解析时报端口越界，只在访问 `.port` 时抛 ValueError；
    # 这里提前触一次，把 `http://host:99999/` 归到 `fetch_invalid_url`，
    # 而不是让它以裸 `ValueError` 冒到工具层（那样会丢掉 error_code）。
    try:
        parts.port
    except ValueError:
        return None
    return parts


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    """判定单个 IP 是否落在禁止网段。

    IPv4-mapped IPv6（`::ffff:127.0.0.1`）先还原成 IPv4 再判，否则会被当成普通
    IPv6 绕过。
    """
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return any(ip in network for network in BLOCKED_NETWORKS)


def _resolve_ips_sync(host: str, port: int) -> list[ipaddress._BaseAddress]:
    """同步解析域名到 IP 列表（在线程池里跑，避免阻塞事件循环）。"""
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    addresses: list[ipaddress._BaseAddress] = []
    for info in infos:
        sockaddr = info[4]
        raw = sockaddr[0]
        try:
            addresses.append(ipaddress.ip_address(raw))
        except ValueError:
            continue
    return addresses


async def _assert_host_allowed(hostname: str, port: int) -> dict[str, Any] | None:
    """校验 hostname 可访问；不允许时返回失败返回体，允许时返回 `None`。

    **必须先解析再判 IP**：只比对字面量会漏掉 `127.0.0.1.nip.io` 这类域名，也会
    漏掉 DNS 记录指向内网的域名。
    """
    lowered = hostname.lower().strip("[]")
    if lowered in BLOCKED_HOSTNAMES:
        return _error(
            "fetch_blocked_host",
            f"拒绝访问内网或回环地址: {hostname}",
        )

    # 字面量 IP 直接判定，不必走 DNS。
    try:
        literal = ipaddress.ip_address(lowered)
    except ValueError:
        literal = None
    if literal is not None:
        if _ip_is_blocked(literal):
            return _error(
                "fetch_blocked_host",
                f"拒绝访问内网或回环地址: {hostname}",
            )
        return None

    loop = asyncio.get_running_loop()
    try:
        addresses = await loop.run_in_executor(None, _resolve_ips_sync, lowered, port)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        return _error("fetch_invalid_url", f"域名解析失败: {hostname}（{exc}）")

    if not addresses:
        return _error("fetch_invalid_url", f"域名解析不到任何 IP: {hostname}")

    for address in addresses:
        if _ip_is_blocked(address):
            return _error(
                "fetch_blocked_host",
                f"拒绝访问内网或回环地址: {hostname} 解析到 {address}",
            )
    return None


# ── HTTP 取数 ────────────────────────────────────────────────────────────


class _FetchAbort(Exception):
    """内部信号：在 `try` 里提前终止并带上已构造好的失败返回体。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload.get("message", ""))
        self.payload = payload


class _Redirect(Exception):
    """内部信号：`_fetch_one` 收到 3xx，交给外层循环换地址重来。

    用异常而不是返回值，是为了让「正常返回」只有一种形态 —— 外层不会拿到
    半成品元组，也不会漏判。
    """

    def __init__(self, next_url: str, status: int) -> None:
        super().__init__(f"redirect {status} -> {next_url}")
        self.next_url = next_url
        self.status = status


def _content_type_of(response: aiohttp.ClientResponse) -> tuple[str, str | None]:
    """拆出 `(主类型, charset)`，都转小写。"""
    raw = response.headers.get("Content-Type", "") or ""
    parts = [piece.strip() for piece in raw.split(";")]
    mime = parts[0].lower() if parts else ""
    charset = None
    for piece in parts[1:]:
        if piece.lower().startswith("charset="):
            charset = piece.split("=", 1)[1].strip().strip('"').lower() or None
    return mime, charset


async def _read_limited(response: aiohttp.ClientResponse, url: str) -> bytes:
    """按块读响应体，超 `MAX_BODY_BYTES` 立即中止。"""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise _FetchAbort(
                _error(
                    "fetch_too_large",
                    f"响应体超过 {MAX_BODY_BYTES} 字节上限，已中止读取: {url}",
                )
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_body(body: bytes, charset: str | None) -> str:
    """按 `Content-Type` charset 解码，缺失或未知时回退 `charset-normalizer`。"""
    candidates: list[str] = []
    if charset:
        candidates.append(charset)
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(body).best()
        if best is not None and best.encoding:
            candidates.append(best.encoding)
    except Exception:  # 探测失败不该让整次抓取失败
        pass
    candidates.append("utf-8")

    for encoding in candidates:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


async def _fetch_one(
    session: aiohttp.ClientSession,
    url: str,
) -> tuple[str, str, str | None, bytes]:
    """请求单个 URL（不跟随重定向），返回 `(final_url, mime, charset, body)`。

    重定向交给 `fetch_markdown` 的循环处理 —— 只有这样才能**每一跳都重新做
    SSRF 校验**。
    """
    parts = _split_url(url)
    if parts is None:
        raise _FetchAbort(_error("fetch_invalid_url", f"不是合法的 http/https 地址: {url}"))

    blocked = await _assert_host_allowed(parts.hostname or "", parts.port or 0)
    if blocked is not None:
        raise _FetchAbort(blocked)

    async with session.get(url, allow_redirects=False, ssl=_ssl_context()) as response:
        if response.status in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            if not location:
                raise _FetchAbort(
                    _error("fetch_http_error", f"HTTP {response.status} 重定向缺少 Location: {url}")
                )
            raise _Redirect(urljoin(url, location), response.status)
        if response.status >= 400:
            raise _FetchAbort(
                _error("fetch_http_error", f"HTTP {response.status}: {url}")
            )
        if response.status >= 300:
            raise _FetchAbort(
                _error("fetch_http_error", f"HTTP {response.status}（不支持的 3xx）: {url}")
            )

        mime, charset = _content_type_of(response)
        body = await _read_limited(response, url)
        return str(response.url), mime, charset, body


# ── HTML → Markdown ──────────────────────────────────────────────────────


def html_to_markdown(html: str) -> str:
    """把 HTML 转成 Markdown（一次性固定清洗 + markdownify 默认能力）。

    表格 / 标题 / 代码围栏都靠 markdownify 自身能力保真，**不为表格单独写后处理器**。
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag_name in DROP_TAGS:
        for node in soup.find_all(tag_name):
            node.decompose()

    # `<head>` 里的 title / meta / link 全部不入正文。
    if soup.head is not None:
        soup.head.decompose()

    return _to_markdown(str(soup), heading_style="ATX").strip()


def truncate_content(text: str) -> str:
    """超过 `MAX_CONTENT_CHARS` 即截断，并在末尾写明原文长度。"""
    if len(text) <= MAX_CONTENT_CHARS:
        return text
    original = len(text)
    return (
        text[:MAX_CONTENT_CHARS]
        + f"\n\n[已截断：原文 {original} 字符，此处保留前 {MAX_CONTENT_CHARS} 字符]"
    )


# ── 对外主入口 ───────────────────────────────────────────────────────────


async def fetch_markdown(url: str) -> dict[str, Any]:
    """抓取 URL 并返回 Markdown 正文。

    Args:
        url: 要读取的网页地址，必须是 http/https 绝对地址。

    Returns:
        `{"success": True, "message": <Markdown 正文>, "url": <最终地址>,
        "content_type": <主类型>, "original_chars": <截断前字符数>}`
        或 `{"success": False, "message": ..., "error_code": ...}`。

    Notes:
        **不执行 JavaScript**。SPA / 纯客户端渲染的站点只能取到空壳，此时通常
        返回 `fetch_empty_content`。
    """
    if not isinstance(url, str) or not url.strip():
        return _error("fetch_invalid_url", "url 不能为空")

    timeout = aiohttp.ClientTimeout(
        total=TOTAL_TIMEOUT_SECONDS,
        sock_connect=CONNECT_TIMEOUT_SECONDS,
    )
    # `Accept-Encoding` 不声明 br：避免服务端返回 brotli 而我们没有解码器。
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }

    current_url = url.strip()
    try:
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
            trust_env=False,
        ) as session:
            for _ in range(MAX_REDIRECTS + 1):
                try:
                    final_url, mime, charset, body = await _fetch_one(session, current_url)
                except _Redirect as redirect:
                    current_url = redirect.next_url
                    continue
                except _FetchAbort as abort:
                    return abort.payload

                if mime not in ALLOWED_CONTENT_TYPES:
                    return _error(
                        "fetch_unsupported_content_type",
                        f"不支持的内容类型: {mime or '未声明'}（只支持 HTML / XHTML / 纯文本）",
                    )

                text = _decode_body(body, charset)

                if mime == "text/plain":
                    markdown = text.strip()
                else:
                    try:
                        markdown = html_to_markdown(text)
                    except Exception as exc:  # 转换失败按空内容处理，不抛裸异常
                        return _error(
                            "fetch_empty_content",
                            f"HTML 转换 Markdown 失败: {exc}",
                        )

                if not markdown:
                    return _error(
                        "fetch_empty_content",
                        "页面没有可取文本（可能是纯 JavaScript 渲染的站点，本工具不执行 JS）",
                    )

                original_chars = len(markdown)
                return {
                    "success": True,
                    "message": truncate_content(markdown),
                    "url": final_url,
                    "content_type": mime,
                    "original_chars": original_chars,
                }

            return _error(
                "fetch_http_error",
                f"重定向超过 {MAX_REDIRECTS} 次，已中止: {url}",
            )

    except asyncio.TimeoutError:
        return _error(
            "fetch_timeout",
            f"抓取超时（总超时 {TOTAL_TIMEOUT_SECONDS} 秒）: {url}",
        )
    except aiohttp.ClientPayloadError as exc:
        return _error("fetch_http_error", f"响应体读取失败: {exc}")
    except aiohttp.InvalidURL as exc:
        return _error("fetch_invalid_url", f"非法地址: {exc}")
    except aiohttp.ClientError as exc:
        return _error("fetch_http_error", f"请求失败: {exc}")
