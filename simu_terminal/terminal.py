"""
TerminalProcess — manages a PTY child process and a pyte virtual screen.
"""

import fcntl
import os
import select
import signal
import struct
import sys
import termios
import threading

import pyte

from .render import COLS, ROWS, render_to_png_bytes


class TerminalProcess:
    def __init__(self, cmd: list[str], cols: int = COLS, rows: int = ROWS):
        self.cmd   = cmd
        self.cols  = cols
        self.rows  = rows

        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self._lock  = threading.Lock()

        self._master_fd: int | None = None
        self._pid: int | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import pty
        master_fd, slave_fd = pty.openpty()

        pid = os.fork()
        if pid == 0:
            # child
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", self.rows, self.cols, 0, 0))
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            os.close(master_fd)
            os.close(slave_fd)
            os.execvp(self.cmd[0], self.cmd)
            sys.exit(1)

        # parent
        os.close(slave_fd)
        self._master_fd = master_fd
        self._pid = pid
        self._running = True

        self._thread = threading.Thread(target=self._reader_thread, daemon=True)
        self._thread.start()

    def _reader_thread(self) -> None:
        while self._running:
            try:
                r, _, _ = select.select([self._master_fd], [], [], 0.1)
            except (ValueError, OSError):
                break
            if r:
                try:
                    data = os.read(self._master_fd, 4096)
                except OSError:
                    break
                with self._lock:
                    self.stream.feed(data)

    def send_input(self, data: bytes) -> None:
        if self._master_fd is not None:
            os.write(self._master_fd, data)

    def screenshot(self) -> bytes:
        with self._lock:
            # render_to_png_bytes reads screen under lock
            return render_to_png_bytes(self.screen)

    def stop(self) -> None:
        self._running = False
        if self._pid is not None:
            try:
                os.kill(self._pid, signal.SIGTERM)
                os.waitpid(self._pid, 0)
            except Exception:
                pass
            self._pid = None
        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
            self._master_fd = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
