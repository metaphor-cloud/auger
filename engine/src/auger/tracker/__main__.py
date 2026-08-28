"""Start the work tracker on stdin and stdout.

An agent starts this itself, from inside a repository. It opens no port and it holds no
token, so nothing else on the machine can reach it, and it needs no running engine.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from auger import __version__
from auger.config.loader import home_dir
from auger.store.db import Store
from auger.tracker.repo import repository_for
from auger.tracker.server import build


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="auger tracker",
        description="Work items for one repository, over MCP.",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="The repository to work on. The default is the one this directory is in.",
    )
    parser.add_argument(
        "--home",
        default=None,
        help="The auger home. The default is AUGER_HOME, or ~/.auger.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse(argv)
    repository = (
        Path(arguments.repo).expanduser().absolute()
        if arguments.repo
        else repository_for(Path.cwd())
    )
    if repository is None:
        # Nothing useful can be recorded against a directory that is not a checkout.
        print(
            "auger tracker: this directory is not in a git repository. "
            "Start it inside one, or pass --repo.",
            file=sys.stderr,
        )
        return 2

    home = Path(arguments.home).expanduser().absolute() if arguments.home else home_dir()
    store = Store.open(home)
    try:
        build(store, repository, __version__).run("stdio")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
