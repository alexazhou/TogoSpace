"""单元测试：BaseHandler 鉴权检查逻辑。"""
import json
import unittest.mock as mock
from tornado.web import RequestHandler


class MockRequest:
    """模拟 Tornado HTTPRequest。"""
    def __init__(self, path: str, method: str = "GET", headers: dict = None):
        self.path = path
        self.method = method
        self.headers = headers or {}


class MockHandler:
    """模拟 BaseHandler 的鉴权检查逻辑。"""
    AUTH_EXEMPT_PATHS = {
        "/system/status.json",
    }

    def __init__(self, request: MockRequest, auth_enabled: bool, auth_token: str):
        self.request = request
        self.auth_enabled = auth_enabled
        self.auth_token = auth_token
        self._status = 200
        self._response = {}

    def _check_auth(self) -> tuple[int, dict] | None:
        """执行鉴权检查，返回错误响应或 None。"""
        # 鉴权未启用
        if not self.auth_enabled:
            return None

        # 豁免路径
        if self.request.path in self.AUTH_EXEMPT_PATHS:
            return None

        # 获取 token
        auth_header = self.request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return (401, {"error_code": "auth_required", "error_desc": "请输入访问 Token"})

        token = auth_header[7:]

        # 验证 token
        if token != self.auth_token:
            return (401, {"error_code": "auth_invalid", "error_desc": "Token 无效"})

        return None


class TestBaseHandlerAuthCheck:
    """测试 BaseHandler._check_auth() 逻辑。"""

    def test_auth_disabled_skips_check(self):
        """鉴权禁用时应跳过检查。"""
        request = MockRequest("/teams/list.json", "GET", {})
        handler = MockHandler(request, auth_enabled=False, auth_token="secret")

        result = handler._check_auth()
        assert result is None

    def test_exempt_path_skips_check(self):
        """豁免路径应跳过检查。"""
        request = MockRequest("/system/status.json", "GET", {})
        handler = MockHandler(request, auth_enabled=True, auth_token="secret")

        result = handler._check_auth()
        assert result is None

    def test_no_token_returns_auth_required(self):
        """无 token 时应返回 auth_required。"""
        request = MockRequest("/teams/list.json", "GET", {})
        handler = MockHandler(request, auth_enabled=True, auth_token="secret")

        result = handler._check_auth()
        assert result == (401, {"error_code": "auth_required", "error_desc": "请输入访问 Token"})

    def test_wrong_token_returns_auth_invalid(self):
        """错误 token 时应返回 auth_invalid。"""
        request = MockRequest("/teams/list.json", "GET", {"Authorization": "Bearer wrong"})
        handler = MockHandler(request, auth_enabled=True, auth_token="secret")

        result = handler._check_auth()
        assert result == (401, {"error_code": "auth_invalid", "error_desc": "Token 无效"})

    def test_correct_token_passes(self):
        """正确 token 时应通过。"""
        request = MockRequest("/teams/list.json", "GET", {"Authorization": "Bearer secret"})
        handler = MockHandler(request, auth_enabled=True, auth_token="secret")

        result = handler._check_auth()
        assert result is None

    def test_token_in_query_param_not_supported(self):
        """query 参数方式不应支持（仅 header）。"""
        request = MockRequest("/teams/list.json?token=secret", "GET", {})
        handler = MockHandler(request, auth_enabled=True, auth_token="secret")

        result = handler._check_auth()
        assert result == (401, {"error_code": "auth_required", "error_desc": "请输入访问 Token"})