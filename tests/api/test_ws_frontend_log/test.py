"""端到端：前端 log 真的经 ws 到了后端盘上（#197）。

**为什么要真起后端**：验收口径明确「不能只验后端起了一个接收接口」。这里走的是
真实链路 —— 真后端子进程 + 真 ws 连接 + 真发报文 + **读盘上的文件**。任何一环
（路由没注册、handler 没分派、logger 没挂 handler、路径算错）断了都会红。

两个类分别覆盖开关的两侧：
- `TestFrontendLogEnabled`：开关打开 ⇒ 盘上确有该文件、内容含前端日志；
- `TestFrontendLogDisabled`：开关关闭（默认）⇒ 该文件/该目录**都不存在**。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

import aiohttp

from ...base import ServiceTestCase

if os.name == "posix" and sys.platform == "darwin":
    os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

_ENV_SWITCH = "TOGOSPACE_FRONTEND_DEBUG"


class _FrontendLogCase(ServiceTestCase):
    """给后端子进程注入 `STORAGE_ROOT` / 开关，并据此推算盘上路径。

    必须显式给 `STORAGE_ROOT`：默认会落到仓库的 `dev_storage_root/`，那样两个测试类
    共用同一个目录，而且会在仓库里留下文件。临时目录也顺带让「文件是否存在」这个
    判据变干净 —— 不用去猜上一次跑留下的残留。
    """

    requires_backend = True

    #: 子类指定后端子进程要看到的开关值；None 表示不设该环境变量。
    backend_switch: str | None = None

    _storage_root: str = ""

    @classmethod
    def _start_backend(cls):
        previous_root = os.environ.get("STORAGE_ROOT")
        previous_switch = os.environ.get(_ENV_SWITCH)

        cls._storage_root = tempfile.mkdtemp(prefix="togospace-frontend-log-")
        os.environ["STORAGE_ROOT"] = cls._storage_root
        if cls.backend_switch is None:
            os.environ.pop(_ENV_SWITCH, None)
        else:
            os.environ[_ENV_SWITCH] = cls.backend_switch
        try:
            super()._start_backend()
        finally:
            # 只影响 spawn 出来的子进程；父进程里 appPaths 早在 import 时就定型了。
            for key, previous in (("STORAGE_ROOT", previous_root), (_ENV_SWITCH, previous_switch)):
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous

    @classmethod
    def teardown_class(cls):
        try:
            super().teardown_class()
        finally:
            if cls._storage_root:
                shutil.rmtree(cls._storage_root, ignore_errors=True)

    @property
    def frontend_log_path(self) -> str:
        # 与 `appPaths.LOGS_DIR` + `BACKEND_LOG_CONFIG["util.frontendLog"]["path"]` 一致
        return os.path.join(self._storage_root, "logs", "backend", "frontend", "frontend.log")

    async def _status(self, session: aiohttp.ClientSession) -> dict:
        async with session.get(f"{self.backend_base_url}/system/status.json") as resp:
            assert resp.status == 200
            return await resp.json()

    async def _send_frames(self, frames: list[dict]) -> None:
        """连一条 ws，把报文按顺序发出去，然后关连接。"""
        ws_url = f"ws://127.0.0.1:{self.backend_port}/ws/events.json"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                for frame in frames:
                    await ws.send_str(json.dumps(frame))
                # 后端是异步处理：给 handler 一点时间把回调跑完再断开。
                await asyncio.sleep(0.5)

    def _wait_for_content(self, needle: str, timeout: float = 5.0) -> str:
        """轮询日志文件直到出现 `needle`，返回最终全文。

        轮询而不是 `sleep` 固定时长：ws 消息落地有排队，写死在「等 1 秒」上是靠运气。
        """
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            if os.path.exists(self.frontend_log_path):
                with open(self.frontend_log_path, encoding="utf-8") as handle:
                    last = handle.read()
                if needle in last:
                    return last
            time.sleep(0.05)
        raise AssertionError(
            f"{timeout}s 内未在 {self.frontend_log_path} 里看到 {needle!r}；"
            f"文件存在={os.path.exists(self.frontend_log_path)} 当前内容={last!r}"
        )


class TestFrontendLogEnabled(_FrontendLogCase):
    """开关打开：前端日志必须落到后端本地文件。"""

    backend_switch = "1"

    async def test_frontend_log_reaches_file_with_level_and_timestamp(self):
        """端到端读数：发一条带中文/级别/时间戳的日志，盘上必须能读到它。

        断言同时看四样：正文、级别、前端时间戳、连接标识。只断言正文足够说明「到了」，
        但级别与时间戳是交付标准里「带足以定位问题的时间/级别信息」的原话。
        """
        async with aiohttp.ClientSession() as session:
            status = await self._status(session)
        assert status["frontend_debug"] is True

        await self._send_frames(
            [
                {
                    "type": "frontend_log",
                    "level": "error",
                    "message": "e2e-前端日志-唯一标记-7f3a",
                    "ts": 1700000000123,
                }
            ]
        )

        content = self._wait_for_content("e2e-前端日志-唯一标记-7f3a")
        line = next(item for item in content.splitlines() if "e2e-前端日志-唯一标记-7f3a" in item)
        assert "level=error" in line
        assert "frontend_ts=2023-11-14T22:13:20.123Z" in line
        assert "client=" in line
        assert " - " in line  # 后端接收时间：`asctime - name - levelname - message`

    async def test_multiple_frames_all_persisted_in_order(self):
        """多条报文按发送顺序落盘，且不丢。

        顺序是排查的判据（「前一条日志之后就崩了」），乱序会直接毁掉这个信息。
        """
        markers = [f"e2e-seq-{index}" for index in range(5)]
        await self._send_frames(
            [
                {"type": "frontend_log", "level": "log", "message": marker}
                for marker in markers
            ]
        )

        content = self._wait_for_content(markers[-1])
        positions = [content.index(marker) for marker in markers]
        assert positions == sorted(positions), f"落盘顺序与发送顺序不一致: {positions}"

    async def test_newline_in_message_cannot_forge_a_log_line(self):
        """正文里的换行被转义：客户端不能伪造出一整行「后端日志」。

        这是安全边界，不是格式洁癖 —— 伪造出的行会被当成后端自己的记录去读。
        """
        await self._send_frames(
            [
                {
                    "type": "frontend_log",
                    "level": "log",
                    "message": "first\n2026-01-01 00:00:00.000 - fake - ERROR - forged",
                }
            ]
        )

        content = self._wait_for_content("forged")
        forged_lines = [line for line in content.splitlines() if line.startswith("2026-01-01 00:00:00.000")]
        assert forged_lines == [], f"客户端伪造出了一整行日志: {forged_lines}"

    async def test_log_persisted_without_auth_frame_when_auth_disabled(self):
        """鉴权关闭（测试默认配置）时，不先发 `auth` 也能落盘。

        这条守住的是「功能在默认部署形态下可用」：鉴权关闭时前端不会发认证帧。
        """
        async with aiohttp.ClientSession() as session:
            status = await self._status(session)
        assert status["auth_enabled"] is False

        await self._send_frames(
            [{"type": "frontend_log", "level": "warn", "message": "e2e-no-auth-needed"}]
        )
        self._wait_for_content("e2e-no-auth-needed")


class TestFrontendLogDisabled(_FrontendLogCase):
    """开关关闭（默认）：不产生文件。"""

    backend_switch = None

    async def test_status_reports_frontend_debug_false(self):
        """`/system/status.json` 必须把开关状态如实暴露给前端。

        前端据此决定发不发报文；这个字段若恒为 true，开关在浏览器形态下等于没有。
        """
        async with aiohttp.ClientSession() as session:
            status = await self._status(session)
        assert status["frontend_debug"] is False

    async def test_no_file_created_and_frame_rejected(self):
        """即使客户端硬发 `frontend_log`，开关关着也不能在盘上留下任何东西。

        注意这里是「客户端不听话」的场景：前端自觉不是安全边界，后端必须自己拦。
        """
        await self._send_frames(
            [
                {"type": "frontend_log", "level": "error", "message": "e2e-should-not-persist"},
                {"type": "frontend_log", "level": "log", "message": "e2e-should-not-persist-2"},
            ]
        )

        logs_dir = os.path.join(self._storage_root, "logs", "backend")
        assert not os.path.exists(self.frontend_log_path), "开关关闭时不应生成 frontend.log"
        assert not os.path.exists(os.path.dirname(self.frontend_log_path)), (
            "开关关闭时连 frontend/ 目录都不应生成"
        )
        # 正向对照：后端日志体系确实在工作，避免「整条日志链路都没起来」造成的假绿。
        assert os.path.exists(os.path.join(logs_dir, "backend.log"))
