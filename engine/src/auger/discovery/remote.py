"""Read the forge coordinates out of a git remote URL.

The organisation level of the policy needs a stable key for every repository, and the
remote is the only source of one. A repository with no remote gets no organisation
settings, only the defaults and its own path entry.
"""

from __future__ import annotations

import re

from auger.models import Remote

#: `git@host:namespace/name.git`, the form that ssh remotes use.
SCP_LIKE = re.compile(r"^(?:(?P<user>[^@/]+)@)?(?P<host>[^:/]+):(?P<path>.+)$")
#: `scheme://[user@]host[:port]/namespace/name.git`
URL_LIKE = re.compile(
    r"^(?P<scheme>[a-z][a-z0-9+.-]*)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+)$"
)


def parse_remote(url: str) -> Remote | None:
    """Return the forge coordinates, or None when the URL names no forge."""
    url = url.strip()
    if not url:
        return None
    if "://" in url:
        match = URL_LIKE.match(url)
        # `file://` names a path on this machine, not a forge.
        if match is None or match.group("scheme") == "file":
            return None
    else:
        match = SCP_LIKE.match(url)
        if match is None:
            return None
    host = match.group("host").lower()
    parts = [part for part in match.group("path").split("/") if part]
    if not parts:
        return None
    name = parts[-1].removesuffix(".git")
    if not name:
        return None
    return Remote(host=host, namespace="/".join(parts[:-1]), name=name)
