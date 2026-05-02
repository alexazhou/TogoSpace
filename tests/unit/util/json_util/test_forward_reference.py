"""测试 jsonUtil 对前向引用（Forward Reference）的支持。

前向引用是指类定义中引用自身类型的注解，例如：
    class TreeNode:
        children: List["TreeNode"]  # "TreeNode" 是字符串形式的前向引用

这种情况在使用 `from __future__ import annotations` 时会自动发生（PEP 563）。
"""

import json
from typing import List

import pytest

from util import jsonUtil


class TreeNode:
    """模拟部门树结构的测试类，包含前向引用类型 List["TreeNode"]。

    类似于 GtDept 的结构：
    - 有基础字段（id, name 等）
    - 有列表字段（agent_ids）
    - 有前向引用的 children 字段
    """
    id: int
    name: str
    manager_id: int
    agent_ids: list[int]
    children: List["TreeNode"]

    def __init__(self, id: int = None, name: str = "", manager_id: int = None):
        self.id = id
        self.name = name
        self.manager_id = manager_id
        self.agent_ids = []
        self.children = []

    def __eq__(self, other):
        return (type(self) == type(other) and
                self.id == other.id and
                self.name == other.name and
                self.manager_id == other.manager_id and
                self.agent_ids == other.agent_ids and
                len(self.children) == len(other.children) and
                all(c1 == c2 for c1, c2 in zip(self.children, other.children)))

    def to_json(self) -> dict:
        """模拟 GtDept 的 to_json 方法。"""
        result = {
            "id": self.id,
            "name": self.name,
            "manager_id": self.manager_id,
            "agent_ids": self.agent_ids,
        }
        if self.children:
            result["children"] = [child.to_json() for child in self.children]
        return result


class TestForwardReference:
    """测试 jsonUtil 对前向引用类型的支持。"""

    def test_simple_forward_reference(self):
        """测试简单的前向引用类型 List["TreeNode"] 解析。"""
        tree_json = '''{
            "id": 1,
            "name": "root",
            "manager_id": 10,
            "agent_ids": [10, 20, 30],
            "children": [
                {
                    "id": 2,
                    "name": "child",
                    "manager_id": 20,
                    "agent_ids": [20, 21],
                    "children": []
                }
            ]
        }'''

        result = jsonUtil.json_load(tree_json, TreeNode)

        assert result.id == 1
        assert result.name == "root"
        assert result.manager_id == 10
        assert result.agent_ids == [10, 20, 30]
        assert len(result.children) == 1
        assert result.children[0].id == 2
        assert result.children[0].name == "child"
        assert isinstance(result.children[0], TreeNode)

    def test_deeply_nested_forward_reference(self):
        """测试深层嵌套的前向引用解析。"""
        tree_json = '''{
            "id": 1,
            "name": "root",
            "manager_id": 10,
            "agent_ids": [10],
            "children": [
                {
                    "id": 2,
                    "name": "level1",
                    "manager_id": 20,
                    "agent_ids": [20],
                    "children": [
                        {
                            "id": 3,
                            "name": "level2",
                            "manager_id": 30,
                            "agent_ids": [30],
                            "children": [
                                {
                                    "id": 4,
                                    "name": "level3",
                                    "manager_id": 40,
                                    "agent_ids": [40],
                                    "children": []
                                }
                            ]
                        }
                    ]
                }
            ]
        }'''

        result = jsonUtil.json_load(tree_json, TreeNode)

        assert result.id == 1
        assert result.children[0].id == 2
        assert result.children[0].children[0].id == 3
        assert result.children[0].children[0].children[0].id == 4
        assert result.children[0].children[0].children[0].name == "level3"

    def test_multiple_children(self):
        """测试多个子节点的前向引用解析。"""
        tree_json = '''{
            "id": 1,
            "name": "root",
            "manager_id": 10,
            "agent_ids": [10, 20, 30, 40],
            "children": [
                {
                    "id": 2,
                    "name": "child1",
                    "manager_id": 20,
                    "agent_ids": [20, 21],
                    "children": []
                },
                {
                    "id": 3,
                    "name": "child2",
                    "manager_id": 30,
                    "agent_ids": [30, 31],
                    "children": []
                },
                {
                    "id": 4,
                    "name": "child3",
                    "manager_id": 40,
                    "agent_ids": [40, 41],
                    "children": []
                }
            ]
        }'''

        result = jsonUtil.json_load(tree_json, TreeNode)

        assert len(result.children) == 3
        assert result.children[0].name == "child1"
        assert result.children[1].name == "child2"
        assert result.children[2].name == "child3"

    def test_serialization_with_to_json(self):
        """测试通过 to_json 方法序列化包含前向引用的对象。"""
        child1 = TreeNode(id=2, name="child1", manager_id=20)
        child1.agent_ids = [20, 21]

        child2 = TreeNode(id=3, name="child2", manager_id=30)
        child2.agent_ids = [30, 31]

        root = TreeNode(id=1, name="root", manager_id=10)
        root.agent_ids = [10, 20, 30]
        root.children = [child1, child2]

        result_str = jsonUtil.json_dump(root)
        result = json.loads(result_str)

        assert result["id"] == 1
        assert result["name"] == "root"
        assert result["manager_id"] == 10
        assert result["agent_ids"] == [10, 20, 30]
        assert len(result["children"]) == 2
        assert result["children"][0]["id"] == 2
        assert result["children"][1]["id"] == 3

    def test_round_trip(self):
        """测试序列化后反序列化的往返一致性。"""
        child = TreeNode(id=2, name="child", manager_id=20)
        child.agent_ids = [20, 21]

        original = TreeNode(id=1, name="root", manager_id=10)
        original.agent_ids = [10, 20]
        original.children = [child]

        # 序列化后反序列化
        json_str = jsonUtil.json_dump(original)
        result = jsonUtil.json_load(json_str, TreeNode)

        assert result == original
        assert result.id == original.id
        assert result.name == original.name
        assert result.agent_ids == original.agent_ids
        assert len(result.children) == len(original.children)
        assert result.children[0].id == original.children[0].id

    def test_empty_children(self):
        """测试空 children 列表的情况。"""
        tree_json = '''{
            "id": 1,
            "name": "leaf",
            "manager_id": 10,
            "agent_ids": [10],
            "children": []
        }'''

        result = jsonUtil.json_load(tree_json, TreeNode)

        assert result.id == 1
        assert result.name == "leaf"
        assert result.children == []

    def test_no_children_field(self):
        """测试 JSON 中没有 children 字段的情况。"""
        tree_json = '''{
            "id": 1,
            "name": "node",
            "manager_id": 10,
            "agent_ids": [10]
        }'''

        result = jsonUtil.json_load(tree_json, TreeNode)

        assert result.id == 1
        assert result.name == "node"
        # children 会使用默认值空列表
        assert result.children == []
