"""
aiohttp HTTP server for simu_terminal.

Routes:
  POST /input       — {"text": "..."} or {"key": "up"}
  GET  /screenshot  — returns image/png
"""

import asyncio

from aiohttp import web

from .terminal import TerminalProcess

_KEY_MAP: dict[str, bytes] = {
    "up":    b"\x1b[A",
    "down":  b"\x1b[B",
    "left":  b"\x1b[D",
    "right": b"\x1b[C",
    "enter": b"\r",
    "tab":   b"\t",
    "esc":   b"\x1b",
    "ctrl+a": b"\x01",
    "ctrl+b": b"\x02",
    "ctrl+c": b"\x03",
    "ctrl+d": b"\x04",
    "ctrl+e": b"\x05",
    "ctrl+f": b"\x06",
    "ctrl+q": b"\x11",
    "ctrl+r": b"\x12",
    "ctrl+s": b"\x13",
    "ctrl+u": b"\x15",
    "ctrl+w": b"\x17",
    "ctrl+z": b"\x1a",
}


def _key_to_bytes(key: str) -> bytes:
    k = key.lower()
    if k in _KEY_MAP:
        return _KEY_MAP[k]
    # generic ctrl+<letter>
    if k.startswith("ctrl+") and len(k) == 6:
        ch = k[5]
        if "a" <= ch <= "z":
            return bytes([ord(ch) - ord("a") + 1])
    return key.encode("utf-8", errors="replace")


async def _handle_input(request: web.Request, process: TerminalProcess) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="Invalid JSON")

    if "text" in body:
        data = str(body["text"]).encode("utf-8")
    elif "key" in body:
        data = _key_to_bytes(str(body["key"]))
    else:
        raise web.HTTPBadRequest(reason='Require "text" or "key" field')

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, process.send_input, data)
    return web.json_response({"ok": True})


async def _handle_screenshot(request: web.Request, process: TerminalProcess) -> web.Response:
    loop = asyncio.get_event_loop()
    png_bytes = await loop.run_in_executor(None, process.screenshot)
    return web.Response(body=png_bytes, content_type="image/png")


async def serve(cmd: list[str], port: int, cols: int, rows: int) -> None:
    process = TerminalProcess(cmd, cols=cols, rows=rows)
    process.start()

    app = web.Application()
    app.router.add_post("/input",      lambda r: _handle_input(r, process))
    app.router.add_get ("/screenshot", lambda r: _handle_screenshot(r, process))

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    print(f"simu_terminal listening on http://0.0.0.0:{port}", flush=True)
    print(f"  cmd: {cmd}", flush=True)

    try:
        # run until interrupted
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        print("Shutting down …", flush=True)
        process.stop()
        await runner.cleanup()
