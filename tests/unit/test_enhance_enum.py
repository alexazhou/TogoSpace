"""测试 EnhanceEnum 的功能。"""
import pytest
from pydantic import BaseModel

from constants import EnhanceEnum, DriverType


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

    def test_missing_not_found(self):
        """_missing_ 找不到时抛出 ValueError。"""
        with pytest.raises(ValueError):
            DriverType("unknown")

    def test_pydantic_integration(self):
        """Pydantic 模型支持大小写不敏感的枚举转换（仅限字符串 value 的枚举）。"""

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

    def test_repr(self):
        """测试 __repr__ 方法。"""
        assert repr(DriverType.NATIVE) == "[NATIVE]"