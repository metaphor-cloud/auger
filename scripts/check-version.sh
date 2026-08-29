#!/usr/bin/env bash
#
# Check that the tag and all four version fields agree.
#
# The updater compares the running version with the one in latest.json. If the bundle
# says 0.1.0 and the tag says 0.2.0, every copy downloads the update, installs it, and
# offers it again for ever. The check is cheap and that failure is not.
#
# Usage: check-version.sh <version without the leading v>

set -euo pipefail

want="${1:?usage: check-version.sh <version>}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

read_json() { python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['version'])" "$1"; }
read_toml() { grep --max-count=1 '^version = ' "$1" | cut -d '"' -f 2; }

declare -a bad=()
check() {
    if [ "$2" != "${want}" ]; then
        bad+=("$1 says $2")
    fi
}

check "app/src-tauri/tauri.conf.json" "$(read_json "${root}/app/src-tauri/tauri.conf.json")"
check "app/package.json" "$(read_json "${root}/app/package.json")"
check "app/src-tauri/Cargo.toml" "$(read_toml "${root}/app/src-tauri/Cargo.toml")"
check "engine/pyproject.toml" "$(read_toml "${root}/engine/pyproject.toml")"

if [ "${#bad[@]}" -ne 0 ]; then
    echo "the release wants ${want}, but:" >&2
    printf '  %s\n' "${bad[@]}" >&2
    exit 1
fi

echo "every version field says ${want}"
