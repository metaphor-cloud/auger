#!/usr/bin/env bash
#
# Check that all four version fields agree, and that they agree with the tag.
#
# The updater compares the running version with the one in latest.json. If the bundle
# says 0.1.0 and the tag says 0.2.0, every copy downloads the update, installs it, and
# offers it again for ever. The check is cheap and that failure is not.
#
# With a version, the fields have to match it. That is the release check.
#
# With no version, the fields only have to match each other. That is the check every
# branch gets, because four files bumped by hand drift one at a time, and finding out
# at the tag is finding out too late.
#
# Usage: check-version.sh [version without the leading v]

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

read_json() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$1"; }
read_toml() { grep --max-count=1 '^version = ' "$1" | cut -d '"' -f 2; }
read_python() { grep --max-count=1 '^__version__ = ' "$1" | cut -d '"' -f 2; }

declare -a files=(
    "app/src-tauri/tauri.conf.json"
    "app/package.json"
    "app/src-tauri/Cargo.toml"
    "engine/pyproject.toml"
    # What the running engine reports, and what the window shows. A literal, because a
    # frozen sidecar has no package metadata to read it from.
    "engine/src/auger/__init__.py"
)

read_version() {
    case "$1" in
        *.json) read_json "${root}/$1" ;;
        *.py) read_python "${root}/$1" ;;
        *) read_toml "${root}/$1" ;;
    esac
}

# With no argument the first file decides, and the rest have to agree with it.
want="${1:-$(read_version "${files[0]}")}"

declare -a bad=()
for file in "${files[@]}"; do
    found="$(read_version "${file}")"
    if [ "${found}" != "${want}" ]; then
        bad+=("${file} says ${found}")
    fi
done

if [ "${#bad[@]}" -ne 0 ]; then
    echo "the version should be ${want}, but:" >&2
    printf '  %s\n' "${bad[@]}" >&2
    echo "run 'just version ${want}' to set every one." >&2
    exit 1
fi

echo "every version field says ${want}"
