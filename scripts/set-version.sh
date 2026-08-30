#!/usr/bin/env bash
#
# Set the version in all four places at once.
#
# Four files edited by hand drift one at a time, and the drift is invisible until a tag
# turns it into a release that will not install. One command writes all four, and the
# lock files that carry the version with them.
#
# Usage: set-version.sh <version without the leading v>

set -euo pipefail

want="${1:?usage: set-version.sh <version>}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! [[ "${want}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    echo "${want} is not a version. Use x.y.z, with no leading v." >&2
    exit 1
fi

# Only the version at the top of the file. `python3 -c` rather than sed, because the
# JSON files hold other version fields further down and a line match would find them.
python3 - "${want}" "${root}" <<'PY'
import json
import re
import sys
from pathlib import Path

want, root = sys.argv[1], Path(sys.argv[2])

for name in ("app/src-tauri/tauri.conf.json", "app/package.json"):
    path = root / name
    text = path.read_text(encoding="utf-8")
    document = json.loads(text)
    if document.get("version") == want:
        continue
    # Rewriting the parsed document would reformat the whole file, so only the one
    # value is replaced, and only where the key sits at the top level.
    updated, count = re.subn(
        r'^(\s*"version"\s*:\s*")[^"]*(")',
        rf"\g<1>{want}\g<2>",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise SystemExit(f"{name}: could not find a version to set")
    path.write_text(updated, encoding="utf-8")

for name in ("app/src-tauri/Cargo.toml", "engine/pyproject.toml"):
    path = root / name
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'^version = "[^"]*"', f'version = "{want}"', text, count=1, flags=re.MULTILINE
    )
    if count != 1:
        raise SystemExit(f"{name}: could not find a version to set")
    path.write_text(updated, encoding="utf-8")
PY

# The lock files name the package and its version, so they go stale the moment it moves.
# Both of these only resolve what is already there.
(cd "${root}/engine" && uv lock --quiet)
(cd "${root}/app/src-tauri" && cargo update --workspace --quiet)

"${root}/scripts/check-version.sh"
