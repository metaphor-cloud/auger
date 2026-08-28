"""The `auger` command.

`auger` alone serves the engine. The Tauri host starts it, reads the port from the first
log line, and then polls `/health`. Port 0 asks the operating system for a free port,
which avoids a clash with anything else on the machine.

`auger tracker` is the work tracker, on stdin and stdout, for an agent to attach. It is
the same binary under another word, so the packaged application needs to ship only one.
"""

from __future__ import annotations

import socket
import sys

import uvicorn

from auger import __version__
from auger.api import create_app
from auger.log import create_logger
from auger.parent import watch_parent
from auger.rig import Rig
from auger.settings import Settings

USAGE = """\
auger - a background code reviewer that keeps your code on your machine.

  auger              serve the engine and the window
  auger tracker      the work tracker for this repository, over MCP
  auger --version    print the version
"""


def main() -> None:
    first = sys.argv[1:2]
    if first == ["tracker"]:
        from auger.tracker.__main__ import main as tracker

        raise SystemExit(tracker(sys.argv[2:]))
    if first in (["--help"], ["-h"], ["help"]):
        print(USAGE, end="")
        raise SystemExit(0)
    if first in (["--version"], ["-V"]):
        print(__version__)
        raise SystemExit(0)
    if first and first[0].startswith("-") is False:
        print(f"auger: no such command: {first[0]}\n\n{USAGE}", end="", file=sys.stderr)
        raise SystemExit(2)

    settings = Settings.from_env()
    log = create_logger("engine", settings.log_level)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((settings.host, settings.port))
    port = sock.getsockname()[1]

    # The host waits for this line before it polls /health. Keep the field name stable.
    log.info("engine listening", port=port, host=settings.host, version=__version__)

    app = create_app(Rig(settings, log))
    config = uvicorn.Config(app, log_config=None, access_log=False)
    server = uvicorn.Server(config)

    def stop_for_lost_parent() -> None:
        # Stop first, then log. The log goes to a pipe that the dead host owned.
        server.should_exit = True
        log.warn("host process is gone, stopping", reason="parent_gone")

    watch_parent(stop_for_lost_parent)
    server.run(sockets=[sock])


if __name__ == "__main__":
    main()
