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

    def _seed_task(
        self, *, team_id: int, agent_id: int, title: str, priority: str = "NORMAL",
        reviewer_id: int | None = None, updated_at: str | None = None,
    ) -> int:
        """直接写一行任务 + 两条事件，返回 task_id。

        `updated_at` 显式传入时按原样落库（格式 `YYYY-MM-DD HH:MM:SS.ffffff`）：
        排序断言不能依赖「插入顺序 = 时间戳单调」这种巧合，需要可控时间戳时必须写死。
        """
        with sqlite3.connect(self._get_test_db_path()) as conn:
            if updated_at is None:
                cursor = conn.execute(
                    "INSERT INTO agent_tasks (team_id, title, description, assignee_id, creator_id,"
                    " reviewer_id, status, priority, depends_on)"
                    " VALUES (?, ?, '', ?, ?, ?, 'TODO', ?, '[]')",
                    (team_id, title, agent_id, agent_id, reviewer_id, priority),
                )
            else:
                cursor = conn.execute(
                    "INSERT INTO agent_tasks (team_id, title, description, assignee_id, creator_id,"
                    " reviewer_id, status, priority, depends_on, created_at, updated_at)"
                    " VALUES (?, ?, '', ?, ?, ?, 'TODO', ?, '[]', ?, ?)",
                    (team_id, title, agent_id, agent_id, reviewer_id, priority,
                     updated_at, updated_at),
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

    # -----------------------------------------------------------------
    # Web 列表接口（`agentController.py` 的两个 tasks.json）回归
    # 两个 handler 共用 `taskService.list_tasks`，但 order 口径不同，必须分别钉住。
    # -----------------------------------------------------------------

    async def test_agent_tasks_endpoint_orders_by_updated_desc_and_adds_names(self):
        """`GET /agents/<id>/tasks.json`：**最近修改在前**（`updated_at desc, id desc`），并带 name 字段。

        `b310c3f5` 起该接口显式传 `order=ORDER_UPDATED_DESC`（Agent 任务卡看「刚改过的在最上面」），
        不再走 `taskService.list_tasks` 的优先级优先默认值——那个默认值归团队级列表用
        （见下一个用例）。本用例在 **HTTP 层**钉住这个口径。

        三种口径的预期顺序刻意两两不同，断言才有鉴别力：
          - updated_at desc（本接口）：low → high → normal
          - priority first（旧口径）  ：high → normal → low
          - id desc                  ：normal → high → low
        `updated_at` 全部显式写死，不依赖「插入顺序 = 时间戳单调」这种巧合。
        """
        async with aiohttp.ClientSession() as session:
            team_id, agent_id = await self._resolve_ids(session)
            low = self._seed_task(
                team_id=team_id, agent_id=agent_id, title="web low", priority="LOW",
                updated_at="2026-01-01 03:00:00.000000",
            )
            high = self._seed_task(
                team_id=team_id, agent_id=agent_id, title="web high", priority="HIGH",
                updated_at="2026-01-01 02:00:00.000000",
            )
            normal = self._seed_task(
                team_id=team_id, agent_id=agent_id, title="web normal", priority="NORMAL",
                updated_at="2026-01-01 01:00:00.000000",
            )

            async with session.get(f"{self.backend_base_url}/agents/{agent_id}/tasks.json") as resp:
                assert resp.status == 200
                body = await resp.json()

        assert body["success"] is True
        # 同一 DB 里还有别的用例 seed 的任务，只看本用例这三条的相对顺序。
        ids = [t["id"] for t in body["tasks"]]
        picked = [task_id for task_id in ids if task_id in {high, normal, low}]
        assert picked == [low, high, normal], (
            f"应为 updated_at 倒序（low→high→normal），实际 {picked}；"
            f"若是 [high,normal,low] 说明退化成了 priority first，"
            f"若是 [normal,high,low] 说明退化成了 id desc"
        )
        # 顺手钉住「这确实不是优先级排序」：优先级序列不应是 HIGH→NORMAL→LOW
        picked_priorities = [t["priority"] for t in body["tasks"] if t["id"] in {high, normal, low}]
        assert picked_priorities == ["LOW", "HIGH", "NORMAL"]
        # 新增字段是**追加**的，不替换任何既有字段
        task = next(t for t in body["tasks"] if t["id"] == high)
        assert task["assignee_id"] == agent_id
        assert isinstance(task["assignee_name"], str) and task["assignee_name"]
        assert task["reviewer_id"] is None and task["reviewer_name"] is None
        # 既有字段与分页字段都在
        for key in ("id", "title", "status", "priority", "count", "total", "limit"):
            assert key in (task if key in task else body)
        assert body["limit"] >= 1

    async def test_team_tasks_endpoint_keeps_priority_order(self):
        """`GET /teams/<id>/tasks.json`：同一链路，排序同样保持优先级优先。"""
        async with aiohttp.ClientSession() as session:
            team_id, agent_id = await self._resolve_ids(session)
            normal = self._seed_task(team_id=team_id, agent_id=agent_id, title="team normal")
            high = self._seed_task(
                team_id=team_id, agent_id=agent_id, title="team high", priority="HIGH"
            )

            async with session.get(f"{self.backend_base_url}/teams/{team_id}/tasks.json") as resp:
                assert resp.status == 200
                body = await resp.json()

        ids = [t["id"] for t in body["tasks"]]
        assert ids.index(high) < ids.index(normal)
