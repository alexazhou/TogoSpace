"""
simu_terminal — universal terminal emulator HTTP service.

Usage:
  python simu_terminal/main.py --port 8888 -- python tui/main.py --base-url http://127.0.0.1:8080
"""

import argparse
import asyncio
import sys


def main() -> None:
    # Split args on "--" separator
    argv = sys.argv[1:]
    if "--" in argv:
        sep = argv.index("--")
        our_args = argv[:sep]
        cmd = argv[sep + 1:]
    else:
        our_args = argv
        cmd = []

    parser = argparse.ArgumentParser(
        description="Run a command in a PTY and expose keyboard input + screenshot over HTTP.",
        add_help=True,
    )
    parser.add_argument("--port", type=int, default=8888, help="HTTP listen port (default: 8888)")
    parser.add_argument("--cols", type=int, default=140,  help="Terminal width in columns (default: 140)")
    parser.add_argument("--rows", type=int, default=36,   help="Terminal height in rows (default: 36)")
    args = parser.parse_args(our_args)

    if not cmd:
        parser.error("No command specified. Use: simu_terminal/main.py [options] -- <command> [args...]")

    from .server import serve
    asyncio.run(serve(cmd, port=args.port, cols=args.cols, rows=args.rows))


if __name__ == "__main__":
    main()
