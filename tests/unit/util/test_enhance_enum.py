"""测试 EnhanceEnum 的功能。"""
import pytest
from pydantic import BaseModel

from constants import EnhanceEnum, DriverType, RoomType


class TestEnhanceEnum:
    """测试 EnhanceEnum 基类功能。"""

    def test_value_of_exact_match(self):
        """value_of 精确匹配。"""
        assert DriverType.value_of("NATIVE") == DriverType.NATIVE
        assert DriverType.value_of("TSP") == DriverType.TSP

    def test_value_of_case_insensitive(self):
        """value_of 大小写不敏感。"""
        assert DriverType.value_of("native") == DriverType.NATIVE
        assert DriverType.value_of("Native") == DriverType.NATIVE
        assert DriverType.value_of("TSP") == DriverType.TSP
        assert DriverType.value_of("tsp") == DriverType.TSP

    def test_value_of_not_found(self):
        """value_of 找不到时返回 None。"""
        assert DriverType.value_of("unknown") is None
        assert DriverType.value_of("") is None
        assert DriverType.value_of(None) is None

    def test_missing_case_insensitive(self):
        """_missing_ 支持大小写不敏感的 value 匹配。"""
        # 小写匹配
        assert DriverType("native") == DriverType.NATIVE
        assert DriverType("claude_sdk") == DriverType.CLAUDE_SDK
        assert DriverType("tsp") == DriverType.TSP

        # 大写匹配
        assert DriverType("NATIVE") == DriverType.NATIVE
        assert DriverType("CLAUDE_SDK") == DriverType.CLAUDE_SDK
        assert DriverType("TSP") == DriverType.TSP

        # 混合大小写
        assert DriverType("Native") == DriverType.NATIVE
        assert DriverType("Claude_Sdk") == DriverType.CLAUDE_SDK

    def test_missing_supports_name_and_value_for_string_enum(self):
        """_missing_ 同时支持 name/value 匹配（字符串枚举）。"""

        class DemoEnum(EnhanceEnum):
            FOO = "foo-value"
            BAR = "bar-value"

        # 按 name 匹配
        assert DemoEnum("FOO") == DemoEnum.FOO
        assert DemoEnum("foo") == DemoEnum.FOO
        # 按 value 匹配
        assert DemoEnum("foo-value") == DemoEnum.FOO
        assert DemoEnum("BAR-VALUE") == DemoEnum.BAR

    def test_missing_trims_whitespace(self):
        """_missing_ 会去掉字符串首尾空白。"""
        assert DriverType("  native  ") == DriverType.NATIVE
        assert RoomType("  GROUP  ") == RoomType.GROUP

    def test_missing_not_found(self):
        """_missing_ 找不到时抛出 ValueError。"""
        with pytest.raises(ValueError):
            DriverType("unknown")

    def test_pydantic_integration(self):
        """Pydantic 模型支持大小写不敏感的枚举转换（字符串 value 枚举）。"""

        class TestModel(BaseModel):
            driver: DriverType = DriverType.NATIVE

        # 小写
        m1 = TestModel(driver="native")
        assert m1.driver == DriverType.NATIVE

        # 大写
        m2 = TestModel(driver="NATIVE")
        assert m2.driver == DriverType.NATIVE

        # 混合大小写
        m3 = TestModel(driver="Native")
        assert m3.driver == DriverType.NATIVE

        # 枚举值直接传入
        m4 = TestModel(driver=DriverType.TSP)
        assert m4.driver == DriverType.TSP

    def test_missing_supports_enum_name_for_auto_enum(self):
        """_missing_ 支持按 name 匹配 auto() 枚举。"""
        assert RoomType("GROUP") == RoomType.GROUP
        assert RoomType("group") == RoomType.GROUP
        assert RoomType("PRIVATE") == RoomType.PRIVATE
        assert RoomType("private") == RoomType.PRIVATE

    def test_pydantic_integration_for_auto_enum_name(self):
        """Pydantic 模型支持以字符串 name 解析 auto() 枚举。"""

        class TestModel(BaseModel):
            room_type: RoomType = RoomType.GROUP

        m1 = TestModel(room_type="GROUP")
        assert m1.room_type == RoomType.GROUP

        m2 = TestModel(room_type="private")
        assert m2.room_type == RoomType.PRIVATE

    def test_pydantic_integration_for_string_enum_name_and_value(self):
        """Pydantic 支持字符串枚举按 name/value 两种方式解析。"""

        class DemoEnum(EnhanceEnum):
            FOO = "foo-value"
            BAR = "bar-value"

        class TestModel(BaseModel):
            enum_value: DemoEnum = DemoEnum.FOO

        # 按 name 解析
        m1 = TestModel(enum_value="BAR")
        assert m1.enum_value == DemoEnum.BAR

        # 按 value 解析（大小写不敏感）
        m2 = TestModel(enum_value="foo-value")
        assert m2.enum_value == DemoEnum.FOO
        m3 = TestModel(enum_value="BAR-VALUE")
        assert m3.enum_value == DemoEnum.BAR

    def test_repr(self):
        """测试 __repr__ 方法。"""
        assert repr(DriverType.NATIVE) == "[NATIVE]"
