"""#194：webfetch 的 SSRF DNS rebinding TOCTOU 修复 —— 判据用例。

## 在验什么

修复前的缺陷：**校验用一次解析、连接用另一次解析**，两次之间有窗口。攻击者控制
DNS（TTL=0）让第一次返回公网 IP（通过校验）、第二次返回 `127.0.0.1`，请求就落到内网。

修复做法：把校验下沉到注入 `TCPConnector` 的 `_GuardedResolver` —— 它自己解析、
自己校验、**只把通过校验的 IP 交给 connector**，connector 不会也无法再解析一次。

## 为什么必须「断在连接目标上」

「resolver 返回了什么」「resolver 被调了几次」都是我自己的返回值，拿它自证是同义反复。
所以核心观测点是**连接层的 accept 事实**：

- 服务器 **A**（端口 `pa`）= 「攻击者想打到的内网目标」
- 服务器 **B**（端口 `pb`）= 「本应连的合法目标」

「被拦地址真的会被连上」这件事能被观测到，靠的是 `0.0.0.0` 的两个性质：
它在 `BLOCKED_NETWORKS` 里（`0.0.0.0/32`，必须被拦），而 `connect(("0.0.0.0", pa))`
在本机会落到监听 `127.0.0.1:pa` 的服务器上（实测，见报告 §4）。
⇒ 去掉 resolver 里的过滤后，A 的 accept 会从 0 变成 1，用例随之变红。

## 量具自证（否则 `accept == 0` 是可疑的 0）

「0 次 accept」同时兼容「被拦住」和「压根没发起连接」。所以配一条正向对照：
放行地址 ⇒ B 的 accept 必须 ≥ 1（`test_guard_measurement_reads_nonzero_on_allowed_target`）。
只有「应发生时 >0、应拦时 =0」这个组合才成立。

## 边界对照集

`_ip_is_blocked` 的边界样本由 `BLOCKED_NETWORKS` **逐段反推**（每段的 `下界-1` /
`上界+1` 才是真边界），不是按 RFC 印象挑的。⚠️ 该对照集与 `BLOCKED_NETWORKS` 同源，
**若将来改动黑名单，必须同步重算**。
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from service.funcToolService import webFetchTool
from service.funcToolService.core import load_func_tools

# ---------------------------------------------------------------------------
# 夹具：两台可分辨的本地服务器（A=内网目标 / B=合法目标）
# ---------------------------------------------------------------------------


class _Server(ThreadingHTTPServer):
    """记录 accept 事实的最小服务器（本组用例的核心观测点）。"""

    daemon_threads = True

    def __init__(self, address, handler_factory, body: bytes = b"ok"):
        self.accepts: list[tuple] = []
        self.body = body
        super().__init__(address, handler_factory(self))

    def handle_error(self, request, client_address):  # noqa: D102 - 静音预期断连
        pass


def _handler_factory(server: _Server):
    class _H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定
            server.accepts.append(self.client_address)
            body = server.body
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _H


def _start(address: tuple[str, int], body: bytes) -> _Server:
    server = _Server(address, _handler_factory, body=body)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def targets():
    """A（内网目标）与 B（合法目标），都监听 `127.0.0.1`、不同端口。"""
    a = _start(("127.0.0.1", 0), b"<html><body><p>INTERNAL-SECRET</p></body></html>")
    b = _start(("127.0.0.1", 0), b"<html><body><p>PUBLIC-PAGE</p></body></html>")
    try:
        yield SimpleNamespace(a=a, b=b, pa=a.server_address[1], pb=b.server_address[1])
    finally:
        for server in (a, b):
            server.shutdown()
            server.server_close()


@pytest.fixture(autouse=True)
def _loaded_registry():
    load_func_tools()
    yield


@pytest.fixture
def guard(monkeypatch):
    """放行 `127.0.0.1`（本地假服务器），其余走**真实** `_ip_is_blocked`。

    只放行这一个地址是有意的：`0.0.0.0` 仍必须被真实拦下（它是本组的「内网」样本），
    否则用例会退化成「什么都放行」的空转。
    """
    original = webFetchTool._ip_is_blocked

    def patched(ip):
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        if str(ip) == "127.0.0.1":
            return False
        return original(ip)

    monkeypatch.setattr(webFetchTool, "_ip_is_blocked", patched)
    return patched


@pytest.fixture
def dns(monkeypatch):
    """按脚本返回解析结果，模拟 DNS 依次给出不同答案（rebinding 的成因）。

    `script` 是 `list[list[str]]`：第 n 次解析取 `script[n]`，超出则重复最后一项。
    `calls` 记录每次被解析的 host —— 预检与 resolver 各算一次，用于钉住
    「回退过程中没有重新解析」。
    """
    state = SimpleNamespace(script=[["127.0.0.1"]], calls=[])

    def fake_resolve(host: str, port: int):
        index = min(len(state.calls), len(state.script) - 1)
        state.calls.append(host)
        return [ipaddress.ip_address(text) for text in state.script[index]]

    monkeypatch.setattr(webFetchTool, "_resolve_ips_sync", fake_resolve)
    return state


# ---------------------------------------------------------------------------
# 判据 2 + 量具自证：被拦时不连，放行时连
# ---------------------------------------------------------------------------


async def test_all_addresses_blocked_returns_blocked_error(targets, guard, dns):
    """全部地址被拦 ⇒ `fetch_blocked_host`，且两台服务器都没收到连接。

    同时钉住拒绝语义：resolver 用「返回空列表」表达拒绝会炸成
    `RuntimeError: coroutine raised StopIteration`（不是 ClientError，会漏到工具层兜底丢错误码）；
    用 `OSError` 会被包成 `ClientConnectorDNSError` ⇒ 错码成 `fetch_http_error`。

    变异自证：去掉 `_GuardedResolver.resolve` 里的过滤 ⇒ `0.0.0.0` 被交给 connector
    ⇒ 落到服务器 A（`0.0.0.0:pa` 在本机等价于 `127.0.0.1:pa`）⇒ 本用例红。
    """
    dns.script = [["0.0.0.0"]]
    result = await webFetchTool.fetch_markdown(f"http://internal.example:{targets.pa}/")

    assert result["success"] is False, result
    assert result["error_code"] == "fetch_blocked_host", result
    assert targets.a.accepts == [], (
        f"被拦地址 0.0.0.0 竟然被连到了（A accept={targets.a.accepts}）"
        "—— 说明校验没有真正作用到连接层"
    )
    assert targets.b.accepts == [], targets.b.accepts
    # 辅助判据（文案级，只兜「错码恰好也对」的漏网）：不该出现旧路径的特征串。
    for leaked in ("ClientConnectorDNSError", "StopIteration", "fetch_http_error"):
        assert leaked not in result["message"], result["message"]


async def test_guard_measurement_reads_nonzero_on_allowed_target(targets, guard, dns):
    """量具自证：同一套夹具、同一台服务器，放行地址时必须读到非零 accept。

    没有这条，上面那条的 `accept == 0` 无法区分「被拦住」和「压根没发起连接」。
    """
    dns.script = [["127.0.0.1"]]
    result = await webFetchTool.fetch_markdown(f"http://public.example:{targets.pb}/")

    assert result["success"] is True, result
    assert "PUBLIC-PAGE" in result["message"]
    assert len(targets.b.accepts) == 1, targets.b.accepts
    assert targets.a.accepts == [], targets.a.accepts


# ---------------------------------------------------------------------------
# 判据 1（核心）：rebinding —— 预检看到放行地址、连接时看到被拦地址
# ---------------------------------------------------------------------------


async def test_rebinding_second_answer_cannot_reach_internal(targets, guard, dns):
    """第一次解析给放行地址（预检通过），第二次给被拦地址 ⇒ 连接层必须拒绝。

    这正是缺陷的原始形态：修复前「预检通过 + connector 自己再解析」= 落到内网。
    这里预检**走真实逻辑**（不 patch `_assert_host_allowed`），两次解析都会发生。
    """
    dns.script = [["127.0.0.1"], ["0.0.0.0"]]
    result = await webFetchTool.fetch_markdown(f"http://rebind.example:{targets.pa}/")

    assert result["success"] is False, result
    assert result["error_code"] == "fetch_blocked_host", result
    assert targets.a.accepts == [], (
        f"第二次解析到 0.0.0.0 后仍然连到了内网目标（A accept={targets.a.accepts}）"
    )
    assert len(dns.calls) == 2, f"应当发生两次解析（预检 + resolver），实际={dns.calls}"


# ---------------------------------------------------------------------------
# 判据 3：多 IP 回退在同一份已校验结果集内，不重新解析
# ---------------------------------------------------------------------------


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


async def test_multi_ip_fallback_does_not_re_resolve(targets, guard, dns):
    """首个地址连不上 ⇒ 回退到同一份结果集的下一个，且解析次数不增加。

    `0.0.0.1` 在 `BLOCKED_NETWORKS` 之外（`0.0.0.0/32` 只含 `0.0.0.0`），本机连接
    会**立即** `No route to host`（实测，不依赖网络超时）⇒ 天然是一个「已校验但连不上」
    的地址，正好用来验回退。

    回退是 connector 的内建行为（`while addr_infos:` 对**同一份**返回列表逐个尝试）。
    本用例钉两件事：① 真的能回退到第二个；② 回退过程中没有重新解析。

    变异自证：
    - 把回退改成「连不上就重新解析一次」⇒ `dns.calls` 多一次 ⇒ 红
    - 让 resolver 只返回首个地址（`allowed[:1]`）⇒ 连不上、回退不了 ⇒ 红
    """
    dns.script = [["0.0.0.1", "127.0.0.1"]]
    result = await webFetchTool.fetch_markdown(f"http://multi.example:{targets.pb}/")

    assert result["success"] is True, result
    assert "PUBLIC-PAGE" in result["message"]
    assert len(targets.b.accepts) >= 1, targets.b.accepts
    # 预检 1 次 + resolver 1 次 = 2；回退过程中不得再解析。
    assert len(dns.calls) == 2, (
        f"解析被调用了 {len(dns.calls)} 次（hosts={dns.calls}）—— 回退时不应重新解析"
    )


# ---------------------------------------------------------------------------
# 判据 4：重定向每一跳都过同一份校验
# ---------------------------------------------------------------------------


async def test_redirect_to_internal_host_is_blocked(targets, guard, dns):
    """首跳合法域名 → 302 到另一个域名（解析到被拦地址）⇒ 被拒且不连内网。"""

    class _RedirectHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://internal.example:{targets.pa}/")
            self.send_header("Content-Length", "0")
            self.end_headers()

    redirect_server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    redirect_server.daemon_threads = True
    threading.Thread(target=redirect_server.serve_forever, daemon=True).start()
    try:
        # 第 1 次：预检首跳；第 2 次：resolver 首跳；第 3 次：预检次跳；第 4 次：resolver 次跳。
        dns.script = [["127.0.0.1"], ["127.0.0.1"], ["0.0.0.0"], ["0.0.0.0"]]
        result = await webFetchTool.fetch_markdown(
            f"http://public.example:{redirect_server.server_address[1]}/"
        )
    finally:
        redirect_server.shutdown()
        redirect_server.server_close()

    assert result["success"] is False, result
    assert result["error_code"] == "fetch_blocked_host", result
    assert targets.a.accepts == [], (
        f"重定向次跳解析到被拦地址后仍连到了内网目标（A accept={targets.a.accepts}）"
    )


# ---------------------------------------------------------------------------
# 判据 5：既有行为不回归
# ---------------------------------------------------------------------------


async def test_literal_ip_never_uses_resolver(monkeypatch, targets, guard):
    """字面量 IP 走 connector 的短路分支，resolver 不会被调用（既有路径保持）。

    这条同时是「为什么 SSRF 用例必须用域名」的证据：用字面量 IP 写的用例
    resolver 调用数为 0，会「通过」但什么都没验到。
    """
    calls: list[str] = []
    original = webFetchTool._GuardedResolver.resolve

    async def spy(self, host, port=0, family=socket.AF_INET):
        calls.append(host)
        return await original(self, host, port, family)

    monkeypatch.setattr(webFetchTool._GuardedResolver, "resolve", spy)

    result = await webFetchTool.fetch_markdown(f"http://127.0.0.1:{targets.pb}/")

    assert result["success"] is True, result
    assert calls == [], f"字面量 IP 不应走 resolver，实际调用={calls}"
    assert len(targets.b.accepts) == 1


async def test_literal_private_ip_still_blocked_without_connection(targets):
    """字面量内网 IP 仍由预检拦下，且没有任何连接（硬约束 3 不回归）。"""
    result = await webFetchTool.fetch_markdown(f"http://127.0.0.1:{targets.pb}/")

    assert result["success"] is False, result
    assert result["error_code"] == "fetch_blocked_host", result
    assert targets.b.accepts == [], targets.b.accepts


@pytest.mark.parametrize(
    "hostname",
    ["localhost", "LOCALHOST", "localhost.localdomain", "ip6-localhost"],
)
async def test_blocked_hostnames_still_blocked(hostname):
    """`BLOCKED_HOSTNAMES` 名单保持（大小写不敏感）。"""
    result = await webFetchTool._assert_host_allowed(hostname, 80)
    assert result is not None
    assert result["error_code"] == "fetch_blocked_host"


# ---------------------------------------------------------------------------
# 边界对照集（量具自证）：`_ip_is_blocked` 的鉴别力
# ---------------------------------------------------------------------------
#
# ⚠️ 与 `BLOCKED_NETWORKS` **同源**：每条网段取 `下界-1` / `上界+1` 才是真边界。
# 若将来改动黑名单，本表必须同步重算。

#: (地址, 期望是否被拦)。每个网段的「紧邻外侧」必须放行 —— 这是抓「划宽一格」的靶子。
_BOUNDARY_CASES = [
    ("9.255.255.255", False),  # 10.0.0.0/8 下界-1
    ("10.0.0.0", True),
    ("10.255.255.255", True),
    ("11.0.0.0", False),  # 10.0.0.0/8 上界+1
    ("172.15.255.255", False),  # 172.16.0.0/12 下界-1
    ("172.16.0.0", True),
    ("172.31.255.255", True),
    ("172.32.0.0", False),  # 172.16.0.0/12 上界+1
    ("192.167.255.255", False),  # 192.168.0.0/16 下界-1
    ("192.168.0.0", True),
    ("192.168.255.255", True),
    ("192.169.0.0", False),  # 192.168.0.0/16 上界+1
    ("169.253.255.255", False),  # 169.254.0.0/16 下界-1
    ("169.254.0.0", True),
    ("169.254.169.254", True),  # 云元数据端点
    ("169.255.0.0", False),  # 169.254.0.0/16 上界+1
    ("126.255.255.255", False),  # 127.0.0.0/8 下界-1
    ("127.0.0.0", True),
    ("127.255.255.255", True),
    ("128.0.0.0", False),  # 127.0.0.0/8 上界+1
    ("0.0.0.0", True),
    ("0.0.0.1", False),  # 0.0.0.0/32 之外
    ("203.0.113.10", False),  # TEST-NET-3
]


@pytest.mark.parametrize(("address", "blocked"), _BOUNDARY_CASES)
def test_ip_boundary_matrix(address, blocked):
    """边界样本逐条命中；「紧邻段」全部放行才能证明没有「划宽一格」。"""
    assert webFetchTool._ip_is_blocked(ipaddress.ip_address(address)) is blocked, address


@pytest.mark.parametrize(
    "address",
    ["::ffff:127.0.0.1", "::ffff:169.254.169.254", "::ffff:10.0.0.1"],
)
def test_ipv4_mapped_ipv6_still_blocked(address):
    """IPv4-mapped IPv6 还原路径保持（否则可绕过黑名单）。"""
    assert webFetchTool._ip_is_blocked(ipaddress.ip_address(address)) is True
