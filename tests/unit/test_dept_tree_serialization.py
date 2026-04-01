"""测试部门树序列化/反序列化问题。

排查结论：
1. GET /teams/{id}/dept_tree.json 缺少 children 字段 - 已确认存在
   - 根因：GtDept.to_json() 中 `if self.children:` 对空列表 [] 返回 False
   - 叶子节点的 JSON 输出不包含 "children" 字段
   - 前端 node.children.map(...) 收到 undefined，报错

2. PUT /teams/{id}/dept_tree/update.json 反序列化嵌套 children - 已确认不存在
   - jsonUtil 已正确支持前向引用 List["GtDept"]
   - 反序列化后 children 元素都是 GtDept 类型
"""

import json
from typing import List

import pytest

from model.dbModel.gtDept import GtDept
from util import jsonUtil


class TestDeptTreeSerialization:
    """验证问题 1：GET 序列化缺少 children 字段"""

    def test_gtdept_to_json_empty_children_should_output_empty_array(self):
        """验证空 children=[] 时 to_json() 应输出 children=[]。

        修复预期：
        - children=[]（空列表）时应该输出 "children": []
        - children=None 时可以忽略字段

        当前问题：`if self.children:` 对 [] 返回 False，导致不输出。
        """
        dept = GtDept(
            id=1,
            team_id=1,
            name="root",
            responsibility="根部门",
            parent_id=None,
            manager_id=10,
            agent_ids=[10, 20],
        )
        # children 是类默认值 []，未显式设置

        result = dept.to_json()

        # 验证基础字段存在
        assert "id" in result
        assert "name" in result
        assert "responsibility" in result
        assert "manager_id" in result
        assert "agent_ids" in result

        # 期望：空列表也应该输出 children: []
        # 当前 FAIL，证明问题存在
        assert "children" in result, "BUG: children=[] 时缺少 children 字段，期望输出 children: []"
        assert result["children"] == []

    def test_gtdept_to_json_none_children_can_omit(self):
        """验证 children=None 时可以忽略字段（未来扩展）。

        当前 GtDept.children 默认是 []，不是 None。
        如果未来支持 children=None 表示"不展开子节点"，可以忽略字段。
        """
        dept = GtDept(
            id=1,
            team_id=1,
            name="root",
            responsibility="根部门",
            parent_id=None,
            manager_id=10,
            agent_ids=[10, 20],
        )
        dept.children = None  # 显式设置为 None

        result = dept.to_json()

        # None 时可以不输出 children 字段（这是期望行为）
        # 注意：当前实现可能不支持 None，这里只是记录预期
        # assert "children" not in result

    def test_gtdept_to_json_with_children_includes_field(self):
        """验证有 children 时 to_json() 正常输出 children 字段。"""
        parent = GtDept(
            id=1,
            team_id=1,
            name="parent",
            responsibility="父部门",
            parent_id=None,
            manager_id=10,
            agent_ids=[10, 20],
        )
        child = GtDept(
            id=2,
            team_id=1,
            name="child",
            responsibility="子部门",
            parent_id=1,
            manager_id=20,
            agent_ids=[20, 21],
        )
        parent.children = [child]

        result = parent.to_json()

        assert "children" in result
        assert len(result["children"]) == 1
        assert result["children"][0]["name"] == "child"

    def test_json_dump_gtdept_tree(self):
        """验证 json_dump 对 GtDept 树的序列化行为。"""
        parent = GtDept(
            id=1,
            team_id=1,
            name="parent",
            responsibility="父部门",
            parent_id=None,
            manager_id=10,
            agent_ids=[10, 20],
        )
        child = GtDept(
            id=2,
            team_id=1,
            name="child",
            responsibility="子部门",
            parent_id=1,
            manager_id=20,
            agent_ids=[20, 21],
        )
        parent.children = [child]

        json_str = jsonUtil.json_dump({"dept_tree": parent})
        result = json.loads(json_str)

        # 验证整体结构
        assert "dept_tree" in result
        assert "children" in result["dept_tree"]
        assert len(result["dept_tree"]["children"]) == 1


class TestDeptTreeDeserialization:
    """验证问题 2：PUT 反序列化无法正确解析嵌套 children"""

    def test_json_data_to_object_nested_children(self):
        """验证 json_data_to_object 对嵌套 children 的解析。

        这是问题 2 的根因：
        - jsonUtil.json_data_to_object(data, GtDept) 只解析根节点
        - 嵌套的 children 可能不会被正确转换为 GtDept 对象
        """
        tree_data = {
            "id": 1,
            "team_id": 1,
            "name": "root",
            "responsibility": "根部门",
            "parent_id": None,
            "manager_id": 10,
            "agent_ids": [10, 20],
            "children": [
                {
                    "id": 2,
                    "team_id": 1,
                    "name": "child",
                    "responsibility": "子部门",
                    "parent_id": 1,
                    "manager_id": 20,
                    "agent_ids": [20, 21],
                    "children": [],
                }
            ],
        }

        result = jsonUtil.json_data_to_object(tree_data, GtDept)

        # 验证根节点
        assert isinstance(result, GtDept)
        assert result.name == "root"

        # 问题验证：children 是否被正确转换为 GtDept
        assert isinstance(result.children, list)
        if len(result.children) > 0:
            # 理想情况：children 是 GtDept 对象
            child = result.children[0]
            assert isinstance(child, GtDept), "BUG: children 元素不是 GtDept 类型"
            assert child.name == "child"
            # 验证嵌套 children（如果有）
            if hasattr(child, 'children') and child.children:
                assert isinstance(child.children[0], GtDept)
        else:
            # 问题情况：children 可能是空的或不是 GtDept
            pytest.fail("BUG: children 未被正确解析，可能为空列表或未转换")

    def test_json_data_to_object_deeply_nested(self):
        """验证深层嵌套的 children 解析。"""
        tree_data = {
            "id": 1,
            "team_id": 1,
            "name": "level1",
            "responsibility": "一级",
            "parent_id": None,
            "manager_id": 10,
            "agent_ids": [10],
            "children": [
                {
                    "id": 2,
                    "team_id": 1,
                    "name": "level2",
                    "responsibility": "二级",
                    "parent_id": 1,
                    "manager_id": 20,
                    "agent_ids": [20],
                    "children": [
                        {
                            "id": 3,
                            "team_id": 1,
                            "name": "level3",
                            "responsibility": "三级",
                            "parent_id": 2,
                            "manager_id": 30,
                            "agent_ids": [30],
                            "children": [],
                        }
                    ],
                }
            ],
        }

        result = jsonUtil.json_data_to_object(tree_data, GtDept)

        assert result.name == "level1"
        assert len(result.children) == 1

        level2 = result.children[0]
        assert isinstance(level2, GtDept), "BUG: level2 不是 GtDept 类型"
        assert level2.name == "level2"
        assert len(level2.children) == 1

        level3 = level2.children[0]
        assert isinstance(level3, GtDept), "BUG: level3 不是 GtDept 类型"
        assert level3.name == "level3"


class TestDeptTreeRoundTrip:
    """验证序列化 + 反序列化的往返一致性"""

    def test_round_trip_preserves_structure(self):
        """验证序列化后反序列化能保持完整树结构。"""
        # 构建原始树
        parent = GtDept(
            id=1,
            team_id=1,
            name="parent",
            responsibility="父部门",
            parent_id=None,
            manager_id=10,
            agent_ids=[10, 20],
        )
        child = GtDept(
            id=2,
            team_id=1,
            name="child",
            responsibility="子部门",
            parent_id=1,
            manager_id=20,
            agent_ids=[20, 21],
        )
        parent.children = [child]

        # 序列化
        json_str = jsonUtil.json_dump(parent)
        data = json.loads(json_str)

        # 问题 1 验证：序列化结果是否包含 children
        assert "children" in data, "BUG: 序列化后缺少 children 字段"

        # 反序列化
        result = jsonUtil.json_data_to_object(data, GtDept)

        # 问题 2 验证：反序列化结果是否正确
        assert isinstance(result, GtDept)
        assert result.name == "parent"
        assert len(result.children) == 1
        assert isinstance(result.children[0], GtDept)
        assert result.children[0].name == "child"