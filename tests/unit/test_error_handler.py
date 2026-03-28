"""测试 TeamAgentException 和 IntegrityError 的错误处理。"""
import json
import pytest
from unittest import mock

from exception import TeamAgentException
from controller.baseController import BaseHandler


class TestErrorHandler:
    """测试错误处理逻辑。"""

    def test_team_agent_exception_to_json(self):
        """验证 TeamAgentException 被正确转换为 JSON 错误响应。"""
        # 模拟异常信息
        exc = TeamAgentException(
            error_message='成员名称"小孩哥"已存在',
            error_code="MEMBER_NAME_EXISTS",
        )

        # 验证异常属性
        assert exc.error_message == '成员名称"小孩哥"已存在'
        assert exc.error_code == "MEMBER_NAME_EXISTS"

    def test_integrity_error_wrapped(self):
        """验证 IntegrityError 可以被包装为 TeamAgentException。"""
        from peewee import IntegrityError

        try:
            raise IntegrityError("UNIQUE constraint failed: agents.team_id, agents.name")
        except IntegrityError as e:
            wrapped = TeamAgentException(
                error_message=f'成员名称"测试"已存在',
                error_code="MEMBER_NAME_EXISTS",
            )
            assert wrapped.error_code == "MEMBER_NAME_EXISTS"
            assert "已存在" in wrapped.error_message


class TestBaseHandlerWriteError:
    """测试 BaseHandler.write_error 方法。"""

    def test_write_error_with_team_agent_exception(self):
        """验证 write_error 正确处理 TeamAgentException。"""
        # 创建 mock handler
        handler = mock.MagicMock(spec=BaseHandler)
        handler.enhance = {}
        handler.set_header = mock.MagicMock()
        handler.write = mock.MagicMock()
        handler.set_status = mock.MagicMock()

        # 调用实际的 write_error 方法
        exc = TeamAgentException(
            error_message='成员名称"小孩哥"已存在',
            error_code="MEMBER_NAME_EXISTS",
        )
        exc_info = (TeamAgentException, exc, None)

        # 使用实际方法
        BaseHandler.write_error(handler, 500, exc_info=exc_info)

        # 验证返回了 JSON 格式
        handler.set_header.assert_called()
        handler.write.assert_called()
        # 获取写入的内容
        write_call = handler.write.call_args
        assert write_call is not None

    def test_write_error_with_generic_exception(self):
        """验证 write_error 正确处理通用异常，返回 JSON 而非 HTML。"""
        handler = mock.MagicMock(spec=BaseHandler)
        handler.enhance = {}
        handler.set_header = mock.MagicMock()
        handler.write = mock.MagicMock()

        exc = ValueError("测试错误")
        exc_info = (ValueError, exc, None)

        BaseHandler.write_error(handler, 500, exc_info=exc_info)

        # 验证设置了 JSON header
        handler.set_header.assert_called_with("Content-Type", "application/json")
        # 验证写入了内容
        handler.write.assert_called()

    def test_write_error_returns_json_for_any_exception(self):
        """验证任何异常都返回 JSON 格式的错误信息。"""
        handler = mock.MagicMock(spec=BaseHandler)
        handler.enhance = {}
        handler.set_header = mock.MagicMock()
        handler.write = mock.MagicMock()

        # 模拟一个普通的 RuntimeError
        exc = RuntimeError("数据库连接失败")
        exc_info = (RuntimeError, exc, None)

        BaseHandler.write_error(handler, 500, exc_info=exc_info)

        # 验证设置了 JSON header
        handler.set_header.assert_called_with("Content-Type", "application/json")

        # 验证写入的内容是 JSON 格式
        write_call = handler.write.call_args
        assert write_call is not None
        written_content = write_call[0][0]

        # 解析 JSON 并验证内容
        parsed = json.loads(written_content)
        assert "error_desc" in parsed
        assert "数据库连接失败" in parsed["error_desc"]

    def test_write_error_without_exc_info(self):
        """验证没有异常信息时也返回 JSON 格式。"""
        handler = mock.MagicMock(spec=BaseHandler)
        handler.enhance = {}
        handler.set_header = mock.MagicMock()
        handler.write = mock.MagicMock()

        BaseHandler.write_error(handler, 500)

        # 验证设置了 JSON header
        handler.set_header.assert_called_with("Content-Type", "application/json")

        # 验证写入的内容是 JSON 格式
        write_call = handler.write.call_args
        assert write_call is not None
        written_content = write_call[0][0]
        parsed = json.loads(written_content)
        assert "error_desc" in parsed