"""任务只读接口 API 测试：`GET /tasks/<id>.json` 与 `GET /tasks/<id>/events.json`。

覆盖重点：
  - 任务不存在 ⇒ 400 + `task_not_found`；
  - 详情接口返回依赖摘要 + 时间线首屏 + 派生字段；
  - 事件接口按游标分页、`total` 跨页；
  - **Web 口径不截断**：超长 content 必须完整返回（与面向 Agent 的摘要口径相反）。

数据准备：后端是独立子进程，测试进程不持有它的 ormService 绑定，
因此用裸 sqlite3 直接往同一 DB 文件写行（写后立刻提交并关闭连接）。
"""
import os
import sqlite3
import sys

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

_TEAM = "e2e"
_LONG_NOTE = "打回原因：" + "很长的说明" * 30


class TestTaskController(ServiceTestCase):
    requires_backend = True
    requires_mock_llm = True

    async def _resolve_ids(self, session: aiohttp.ClientSession) -> tuple[int, int]:
        """返回 (team_id, agent_id)。"""
        async with session.get(f"{self.backend_base_url}/teams/list.json") as resp:
            assert resp.status == 200
            teams = (await resp.json())["teams"]
        team_id = next(t for t in teams if t["name"] == _TEAM)["id"]

        async with session.get(f"{self.backend_base_url}/teams/{team_id}.json") as resp:
            assert resp.status == 200
            detail = await resp.json()
        return team_id, detail["agents"][0]["id"]

    def _seed_task(self, *, team_id: int, agent_id: int, title: str) -> int:
        """直接写一行任务 + 两条事件，返回 task_id。"""
        with sqlite3.connect(self._get_test_db_path()) as conn:
            cursor = conn.execute(
                "INSERT INTO agent_tasks (team_id, title, description, assignee_id, creator_id,"
                " status, priority, depends_on) VALUES (?, ?, '', ?, ?, 'TODO', 'NORMAL', '[]')",
                (team_id, title, agent_id, agent_id),
            )
            task_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO agent_task_events (task_id, actor_id, from_status, to_status, content)"
                " VALUES (?, ?, NULL, 'TODO', '')",
                (task_id, agent_id),
            )
            conn.execute(
                "INSERT INTO agent_task_events (task_id, actor_id, from_status, to_status, content)"
                " VALUES (?, ?, 'TODO', 'IN_PROGRESS', ?)",
                (task_id, agent_id, _LONG_NOTE),
            )
            conn.commit()
        return task_id

    async def test_task_not_found_returns_error(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.backend_base_url}/tasks/999999.json") as resp:
                assert resp.status == 400
                body = await resp.json()
        assert body["error_code"] == "task_not_found"

    async def test_events_endpoint_rejects_unknown_task(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.backend_base_url}/tasks/999999/events.json") as resp:
                assert resp.status == 400
                body = await resp.json()
        assert body["error_code"] == "task_not_found"

    async def test_detail_returns_timeline_and_derived_fields(self):
        async with aiohttp.ClientSession() as session:
            team_id, agent_id = await self._resolve_ids(session)
            task_id = self._seed_task(team_id=team_id, agent_id=agent_id, title="api detail")

            async with session.get(f"{self.backend_base_url}/tasks/{task_id}.json") as resp:
                assert resp.status == 200
                detail = await resp.json()

        task = detail["task"]
        assert task["id"] == task_id
        assert task["reviewer_id"] is None
        assert "result" not in task and "block_reason" not in task
        assert task["events_total"] == 2
        assert isinstance(task["depends_on_details"], list)
        # 事件按 id 降序（最新在前），与工具层口径一致
        ids = [e["id"] for e in task["events"]]
        assert ids == sorted(ids, reverse=True)

    async def test_detail_does_not_truncate_content(self):
        """Web 口径：超长 content 完整返回（Agent 摘要口径会截断）。"""
        async with aiohttp.ClientSession() as session:
            team_id, agent_id = await self._resolve_ids(session)
            task_id = self._seed_task(team_id=team_id, agent_id=agent_id, title="api full content")

            async with session.get(f"{self.backend_base_url}/tasks/{task_id}.json") as resp:
                detail = await resp.json()
            async with session.get(
                f"{self.backend_base_url}/tasks/{task_id}/events.json"
            ) as resp:
                page = await resp.json()

        assert detail["task"]["last_note"] == _LONG_NOTE
        assert page["events"][0]["content"] == _LONG_NOTE

    async def test_events_cursor_pagination(self):
        async with aiohttp.ClientSession() as session:
            team_id, agent_id = await self._resolve_ids(session)
            task_id = self._seed_task(team_id=team_id, agent_id=agent_id, title="api paging")

            async with session.get(
                f"{self.backend_base_url}/tasks/{task_id}/events.json",
                params={"limit": 1},
            ) as resp:
                page1 = await resp.json()

            assert page1["count"] == 1
            assert page1["total"] == 2

            async with session.get(
                f"{self.backend_base_url}/tasks/{task_id}/events.json",
                params={"limit": 1, "before_id": page1["events"][0]["id"]},
            ) as resp:
                page2 = await resp.json()

        assert page2["count"] == 1
        assert page2["total"] == 2
        assert page2["events"][0]["id"] < page1["events"][0]["id"]
