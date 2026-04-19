import json
from decimal import Decimal
from datetime import datetime
import datetime as dt
from typing import Optional

import pytest

from util import jsonUtil
from util.jsonUtil import JSONConfig


class Demo:
    a: str = None
    b: int = None
    c: Decimal = None
    d: datetime = None
    e: dt.date = None
    f: dt.time = None

    def __init__(self, a, b, c, d=datetime(2020, 1, 1, 1, 5, 0), e=dt.date(2020, 1, 1), f=dt.time(11, 30, 50, 123000)):
        self.a: str = a
        self.b: int = b
        self.c: Decimal = c
        self.d: datetime = d
        self.e: dt.date = e
        self.f: dt.time = f

    def __eq__(self, other):
        return type(self) == type(other) and self.__dict__ == other.__dict__


class DemoSlots:
    __slots__ = ('a', 'b', 'c', 'd')

    def __init__(self, a, b, c, d=datetime(2020, 1, 1, 1, 5, 0)):
        self.a = a
        self.b = b
        self.c = c
        self.d = d

    def __eq__(self, other):
        return type(self) == type(other) and all(getattr(self, s) == getattr(other, s) for s in self.__slots__)


class DemoNested:
    name: str
    value: Demo

    def __init__(self):
        self.name = None
        self.value = None

    def __eq__(self, other):
        return type(self) == type(other) and self.__dict__ == other.__dict__


class DemoList:
    name: str
    sons: list[DemoNested]
    sons2: dict[str, DemoNested]

    def __eq__(self, other):
        return type(self) == type(other) and self.__dict__ == other.__dict__


class DemoSubclass(DemoList):
    name2: str


class DemoNullable:
    a: str | None
    b: int | None

    def __init__(self):
        self.a = None
        self.b = None

    def __eq__(self, other):
        return type(self) == type(other) and self.__dict__ == other.__dict__


class DemoOptional:
    a: Optional[str]
    b: Optional[int]

    def __init__(self):
        self.a = None
        self.b = None

    def __eq__(self, other):
        return type(self) == type(other) and self.__dict__ == other.__dict__


class DemoUnsupportedUnion:
    a: int | str | None

    def __init__(self):
        self.a = None


class DemoBuiltinDict:
    """使用内置 dict 类型（无泛型参数）的类"""
    name: str
    metadata: dict  # 内置 dict，无泛型参数

    def __init__(self):
        self.name = ""
        self.metadata = {}

    def __eq__(self, other):
        return type(self) == type(other) and self.__dict__ == other.__dict__


class DemoBuiltinList:
    """使用内置 list 类型（无泛型参数）的类"""
    name: str
    items: list  # 内置 list，无泛型参数

    def __init__(self):
        self.name = ""
        self.items = []

    def __eq__(self, other):
        return type(self) == type(other) and self.__dict__ == other.__dict__


class DemoMixedBuiltinAndTyping:
    """混合使用内置类型和 typing 类型"""
    name: str
    data: dict  # 内置 dict
    tags: list[str]  # typing list with 泛型参数
    mapping: dict[str, int]  # typing dict with 泛型参数

    def __eq__(self, other):
        return type(self) == type(other) and self.__dict__ == other.__dict__


class DemoNoneLeft:
    a: None | str
    b: None | int

    def __init__(self):
        self.a = None
        self.b = None


def build_demo_instance(a="1", b=2, c=Decimal("3.33"), d=datetime(2020, 12, 15, 17, 59, 30, 111000)):
    return Demo(a, b, c, d)


def build_demo3_instance(name):
    demo = build_demo_instance()
    demo3 = DemoNested()
    demo3.name = name
    demo3.value = demo
    return demo3


def build_demo4_instance(name, length):
    sons = []
    sons2 = {}
    for i in range(length):
        sons.append(build_demo3_instance(str(i)))
        sons2[str(i)] = build_demo3_instance(str(i))

    demo4 = DemoList()
    demo4.name = name
    demo4.sons = sons
    demo4.sons2 = sons2
    return demo4


demo_data = {"a": "1", "b": 2, "c": 3.33, "d": "2020-12-15 17:59:30.111000", "e": "2020-01-01", "f": "11:30:50.123000"}

demo_json_str = '''{
    "a": "1",
    "b": 2,
    "c": 3.33,
    "d": "2020-12-15 17:59:30.111000",
    "e": "2020-01-01",
    "f": "11:30:50.123000"
}'''

demo_json_str_2 = '{"a": "1", "b": 2, "c": 3.33, "d": "20201215 17:59:30.111000", "e": "2020-01-01", "f": "11:30:50.123000"}'

demo_json_str_3 = '''{
    "name": "123",
    "value": {
        "a": "1",
        "b": 2,
        "c": 3.33,
        "d": "2020-12-15 17:59:30.111000",
        "e": "2020-01-01",
        "f": "11:30:50.123000"
    }
}'''

demo_json_str_4 = '''{
    "name": "demo4instance",
    "sons": [
        {
            "name": "0",
            "value": {
                "a": "1",
                "b": 2,
                "c": 3.33,
                "d": "2020-12-15 17:59:30.111000",
                "e": "2020-01-01",
                "f": "11:30:50.123000"
            }
        },
        {
            "name": "1",
            "value": {
                "a": "1",
                "b": 2,
                "c": 3.33,
                "d": "2020-12-15 17:59:30.111000",
                "e": "2020-01-01",
                "f": "11:30:50.123000"
            }
        }
    ],
    "sons2": {
        "0": {
            "name": "0",
            "value": {
                "a": "1",
                "b": 2,
                "c": 3.33,
                "d": "2020-12-15 17:59:30.111000",
                "e": "2020-01-01",
                "f": "11:30:50.123000"
            }
        },
        "1": {
            "name": "1",
            "value": {
                "a": "1",
                "b": 2,
                "c": 3.33,
                "d": "2020-12-15 17:59:30.111000",
                "e": "2020-01-01",
                "f": "11:30:50.123000"
            }
        }
    }
}'''

demo_json_str_5 = '''[
    {"name": "123"},
    {"name": "456"}
]'''

demo_json_str_6 = '''{
    "name": "aaa",
    "name2": "bbb",
    "sons": [],
    "sons2": {}
}'''


class TestJsonUtil:
    """jsonUtil 基础测试"""

    def test_json_dump(self):
        obj = build_demo_instance()
        ret = jsonUtil.json_dump(obj)
        assert ret == demo_json_str

    def test_json_dump_with_config(self):
        obj = build_demo_instance()
        ret = jsonUtil.json_dump(obj, {
            JSONConfig.datetime_format: "%Y%m%d %H:%M:%S.%f",
            JSONConfig.indent: None
        })
        assert ret == demo_json_str_2

    def test_json_load(self):
        demo = build_demo_instance()
        ret = jsonUtil.json_load(demo_json_str, Demo)
        assert ret == demo

    def test_json_load_with_config(self):
        demo = build_demo_instance()
        ret = jsonUtil.json_load(demo_json_str_2, Demo, config={
            JSONConfig.datetime_format: "%Y%m%d %H:%M:%S.%f"
        })
        assert ret == demo

    def test_json_dump_with_nested_object(self):
        demo3 = build_demo3_instance("123")
        ret = jsonUtil.json_dump(demo3)
        assert ret == demo_json_str_3

    def test_json_load_with_nested_object(self):
        demo3 = build_demo3_instance("123")
        ret = jsonUtil.json_load(demo_json_str_3, DemoNested)
        assert ret == demo3

    def test_object_to_json_data(self):
        demo1 = build_demo_instance()
        ret: dict = jsonUtil.object_to_json_data(demo1)
        assert ret == demo_data

    def test_json_data_to_object(self):
        demo1 = build_demo_instance()
        ret: Demo = jsonUtil.json_data_to_object(demo_data, Demo)
        assert ret == demo1

    def test_json_dump_with_nested_list_object(self):
        demo4 = build_demo4_instance('demo4instance', 2)
        ret: str = jsonUtil.json_dump(demo4)
        assert ret == demo_json_str_4

    def test_json_load_with_nested_list_object(self):
        demo4 = build_demo4_instance('demo4instance', 2)
        ret = jsonUtil.json_load(demo_json_str_4, DemoList)
        assert ret == demo4

    def test_object_list(self):
        items: list[DemoNested] = jsonUtil.json_load(demo_json_str_5, list[DemoNested])
        assert len(items) == 2
        assert isinstance(items[0], DemoNested)
        assert items[0].name == "123"

    def test_subclass(self):
        demo5 = DemoSubclass()
        demo5.name = 'aaa'
        demo5.name2 = 'bbb'
        demo5.sons = []
        demo5.sons2 = {}

        ret = jsonUtil.json_load(demo_json_str_6, DemoSubclass)
        assert ret == demo5

    def test_dump_null(self):
        obj = build_demo_instance()
        ret1 = jsonUtil.object_to_json_data(obj)
        assert list(ret1.keys()) == ['a', 'b', 'c', 'd', 'e', 'f']

        obj.f = None
        ret2 = jsonUtil.object_to_json_data(obj)
        assert list(ret2.keys()) == ['a', 'b', 'c', 'd', 'e', 'f']

    def test_union_pipe_nullable(self):
        ret = jsonUtil.json_data_to_object({"a": "hello", "b": 123}, DemoNullable)
        assert ret.a == "hello"
        assert ret.b == 123

        ret_none = jsonUtil.json_data_to_object({"a": None, "b": None}, DemoNullable)
        assert ret_none.a is None
        assert ret_none.b is None

    def test_optional_nullable(self):
        ret = jsonUtil.json_data_to_object({"a": "world", "b": 456}, DemoOptional)
        assert ret.a == "world"
        assert ret.b == 456

    def test_union_none_left_nullable(self):
        ret = jsonUtil.json_data_to_object({"a": "left", "b": 789}, DemoNoneLeft)
        assert ret.a == "left"
        assert ret.b == 789

        ret_none = jsonUtil.json_data_to_object({"a": None, "b": None}, DemoNoneLeft)
        assert ret_none.a is None
        assert ret_none.b is None

    def test_unsupported_union_with_multiple_non_none_types(self):
        with pytest.raises(TypeError, match="Only Optional\\[T\\] / T \\| None is supported"):
            jsonUtil.json_data_to_object({"a": "x"}, DemoUnsupportedUnion)

    def test_builtin_dict_no_generic_params(self):
        """测试内置 dict 类型（无泛型参数）的反序列化"""
        data = {"name": "test", "metadata": {"key1": "value1", "key2": 123}}
        ret = jsonUtil.json_data_to_object(data, DemoBuiltinDict)
        assert ret.name == "test"
        assert ret.metadata == {"key1": "value1", "key2": 123}

    def test_builtin_dict_serialization(self):
        """测试内置 dict 类型的序列化"""
        obj = DemoBuiltinDict()
        obj.name = "test"
        obj.metadata = {"a": 1, "b": 2}
        ret = jsonUtil.object_to_json_data(obj)
        assert ret == {"name": "test", "metadata": {"a": 1, "b": 2}}

    def test_builtin_dict_round_trip(self):
        """测试内置 dict 类型的序列化-反序列化往返"""
        obj = DemoBuiltinDict()
        obj.name = "roundtrip"
        obj.metadata = {"nested": {"deep": True}, "list": [1, 2, 3]}
        json_str = jsonUtil.json_dump(obj)
        ret = jsonUtil.json_load(json_str, DemoBuiltinDict)
        assert ret == obj

    def test_builtin_list_no_generic_params(self):
        """测试内置 list 类型（无泛型参数）的反序列化"""
        data = {"name": "test", "items": ["a", "b", "c", 1, 2, 3]}
        ret = jsonUtil.json_data_to_object(data, DemoBuiltinList)
        assert ret.name == "test"
        assert ret.items == ["a", "b", "c", 1, 2, 3]

    def test_builtin_list_serialization(self):
        """测试内置 list 类型的序列化"""
        obj = DemoBuiltinList()
        obj.name = "test"
        obj.items = [1, 2, {"nested": True}]
        ret = jsonUtil.object_to_json_data(obj)
        assert ret == {"name": "test", "items": [1, 2, {"nested": True}]}

    def test_builtin_list_round_trip(self):
        """测试内置 list 类型的序列化-反序列化往返"""
        obj = DemoBuiltinList()
        obj.name = "roundtrip"
        obj.items = ["x", "y", {"z": 1}, [1, 2]]
        json_str = jsonUtil.json_dump(obj)
        ret = jsonUtil.json_load(json_str, DemoBuiltinList)
        assert ret == obj

    def test_mixed_builtin_and_typing_types(self):
        """测试混合使用内置类型和 typing 类型"""
        data = {
            "name": "mixed",
            "data": {"raw_key": "raw_value"},  # 内置 dict
            "tags": ["tag1", "tag2"],  # typing list[str]
            "mapping": {"k1": 1, "k2": 2}  # typing dict[str, int]
        }
        ret = jsonUtil.json_data_to_object(data, DemoMixedBuiltinAndTyping)
        assert ret.name == "mixed"
        assert ret.data == {"raw_key": "raw_value"}
        assert ret.tags == ["tag1", "tag2"]
        assert ret.mapping == {"k1": 1, "k2": 2}

    def test_empty_builtin_dict_and_list(self):
        """测试空的内置 dict 和 list"""
        data = {"name": "empty", "metadata": {}, "items": []}
        ret = jsonUtil.json_data_to_object(data, DemoBuiltinDict)
        assert ret.metadata == {}

        ret2 = jsonUtil.json_data_to_object(data, DemoBuiltinList)
        assert ret2.items == []

    def test_none_builtin_dict_and_list(self):
        """测试 None 值的内置 dict 和 list"""
        data = {"name": "none_values", "metadata": None, "items": None}
        ret = jsonUtil.json_data_to_object(data, DemoBuiltinDict)
        assert ret.metadata is None

        ret2 = jsonUtil.json_data_to_object(data, DemoBuiltinList)
        assert ret2.items is None
