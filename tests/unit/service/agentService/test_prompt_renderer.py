import pytest

from service.agentService.promptRenderer import render_prompt


def test_render_todo_task_turn_branches():
    """测试 todo_task_turn.j2 的条件分支正确渲染。"""
    # 1. 默认执行人视角（无验收人，无取消权限）
    out = render_prompt(
        "todo_task_turn.j2",
        task_id=101,
        title="测试任务",
        description="任务描述",
        status_value="TODO",
        has_reviewer=False,
        can_cancel=False,
        latest_event_block=None,
    )
    assert "- 任务 ID: 101" in out
    assert "- 若完成，请调用 `update_task` 将状态改为 DONE，并在 `note` 中写明结果。" in out
    assert "REVIEWING" not in out
    assert "你无权自行取消任务" in out
    assert "请调用 `update_task` 将状态改为 CANCELLED" not in out

    # 3. 验收任务带多条事件列表循环
    out3 = render_prompt(
        "review_task_turn.j2",
        task_id=202,
        title="验收多事件",
        description="描述",
        status_value="REVIEWING",
        events=[
            "- 2026-11-04 10:00: 李哥提交验收",
            "- 2026-11-04 10:05: 张三追加评论",
        ],
        max_chars=500,
    )
    assert "最近事件：" in out3
    assert "- 2026-11-04 10:00: 李哥提交验收" in out3
    assert "- 2026-11-04 10:05: 张三追加评论" in out3
    assert "（事件内容超过 500 字会被截断" in out3


def test_render_turn_begin_and_update():
    """测试 turn_begin.j2 和 turn_update.j2 的渲染（支持原生数据与已格式化 YAML）。"""
    # 原生消息结构
    msgs = [{"sender": "Alice", "sendTime": "2026-11-04 12:00", "content": "hello\nworld"}]
    out_native = render_prompt("turn_begin.j2", room_name="room_a", messages=msgs)
    assert "roomName: room_a" in out_native
    assert "sender: Alice" in out_native
    assert "content: |" in out_native  # 多行使用 literal block 样式
    assert "finish_action 结束行动" in out_native

    # 兼容直接传入 yaml_block 字符串
    out_begin = render_prompt("turn_begin.j2", yaml_block="roomName: demo\nmessages: []")
    assert "当前轮到你行动，新消息如下:" in out_begin
    assert "roomName: demo" in out_begin
    assert "finish_action 结束行动" in out_begin

    out_update = render_prompt("turn_update.j2", yaml_block="roomName: demo\nmessages: [content: hi]")
    assert "房间出现了新的补充信息，请在当前工作过程中参考：" in out_update
    assert "content: hi" in out_update



def test_render_task_event_line():
    """测试 task_event_line.j2 的渲染。"""
    line1 = render_prompt(
        "task_event_line.j2",
        time_text="2026-11-04 10:00",
        action="REVIEWING→DONE",
        actor_name="李哥",
        body="验收通过",
    )
    assert line1 == "- 2026-11-04 10:00 REVIEWING→DONE（李哥）：验收通过"

    # 无时间、无 body
    line2 = render_prompt(
        "task_event_line.j2",
        time_text="",
        action="创建为 TODO",
        actor_name="系统",
        body="",
    )
    assert line2 == "- 创建为 TODO（系统）"


def test_render_review_task_turn():
    """测试 review_task_turn.j2 的渲染。"""
    out = render_prompt(
        "review_task_turn.j2",
        task_id=201,
        title="验收任务",
        description="描述",
        status_value="REVIEWING",
        latest_event_block=None,
    )
    assert "你当前被唤醒以处理以下验收任务" in out
    assert "若验收通过" in out
    assert "若验收不通过" in out


def test_render_system_prompt_branches():
    """测试 system_prompt.j2 的条件段渲染。"""
    # 普通成员模式（非 Root Leader、无 Skill、无共享目录）
    out_member = render_prompt(
        "system_prompt.j2",
        base_prompt="",
        identity_prompt="",
        language="zh-CN",
        agent_id=1,
        agent_name="Member",
        template_name="Helper",
        dept_context="",
        template_soul="只做助手",
        workdir="/workspace/member",
        shared_workdir=None,
        is_root_leader=False,
        skills=[],
    )
    assert "# 行动指南" in out_member
    assert "你当前的名字：Member" in out_member
    assert "get_dept_info" in out_member
    assert "你可以使用以下任务工具" in out_member
    assert "你是当前团队的最高负责人" not in out_member
    assert "部门主管职责" not in out_member
    assert "可用技能" not in out_member

    # 团队 Root Leader 模式 + 带 Skill
    mock_skill = type("SkillInfo", (), {"name": "code_review", "description": "代码审查"})()
    out_team = render_prompt(
        "system_prompt.j2",
        base_prompt="",
        identity_prompt="",
        language="zh-CN",
        agent_id=2,
        agent_name="Leader",
        template_name="Manager",
        dept_context="部门信息",
        template_soul="管理团队",
        workdir="/workspace/team",
        shared_workdir="/workspace/shared",
        is_root_leader=True,
        skills=[mock_skill],
    )
    assert "get_dept_info" in out_team
    assert "你可以使用以下任务工具" in out_team
    assert "部门主管职责" in out_team
    assert "依据必须能被指认" in out_team
    assert "约束的举证责任在提出者" in out_team
    assert "投入不产生正确性" in out_team
    assert "你是当前团队的最高负责人" in out_team
    assert "- 个人工作目录（当前工作目录）：/workspace/team\n- 团队共享目录：/workspace/shared" in out_team
    assert "\n\n# 团队与协作工具\n\n## 团队感知与沟通工具" in out_team
    assert "# 可用技能" in out_team
    assert "- code_review: 代码审查" in out_team

    # 传入结构化 dept_entries 时由 to_yaml 过滤器直接格式化
    out_depts = render_prompt(
        "system_prompt.j2",
        base_prompt="",
        identity_prompt="",
        language="zh-CN",
        agent_id=3,
        agent_name="Bob",
        template_name="Dev",
        dept_entries=[
            {
                "部门": "研发部",
                "你在部门中的角色": "成员",
                "部门职责": "负责核心系统研发",
                "本部门主管": "Alice（ID：2）",
                "部门中其他成员": ["Charlie"],
            }
        ],
        template_soul="专注编码",
        workdir="/workspace/bob",
        shared_workdir=None,
        team_id=1,
        is_root_leader=False,
        skills=[],
    )
    assert "你在组织中的位置:" in out_depts
    assert "  - 部门: 研发部" in out_depts
    assert "    你在部门中的角色: 成员" in out_depts
    assert "    部门职责: 负责核心系统研发" in out_depts
    assert "    本部门主管: Alice（ID：2）" in out_depts
    assert "      - Charlie" in out_depts

    # 4. 部门主管模式（非 Root Leader，担任部门主管）
    out_dept_leader = render_prompt(
        "system_prompt.j2",
        base_prompt="",
        identity_prompt="",
        language="zh-CN",
        agent_id=4,
        agent_name="Alice",
        template_name="DevLead",
        dept_entries=[
            {
                "部门": "研发部",
                "你在部门中的角色": "主管",
                "部门职责": "负责核心系统研发",
            }
        ],
        template_soul="把控研发质量",
        workdir="/workspace/alice",
        shared_workdir="/workspace/shared",
        is_leader=True,
        is_root_leader=False,
        skills=[],
    )
    assert "部门主管职责" in out_dept_leader
    assert "依据必须能被指认" in out_dept_leader
    assert "约束的举证责任在提出者" in out_dept_leader
    assert "投入不产生正确性" in out_dept_leader
    assert "你是当前团队的最高负责人" not in out_dept_leader
