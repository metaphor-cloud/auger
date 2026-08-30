#!/usr/bin/env bash
#
# Work out the version a release should carry.
#
# The number comes from the commit messages since the last tag, so nobody has to hold
# "was that a feature or a fix" in their head at the moment they want to ship. The rules
# are the conventional-commit ones:
#
#   a `!` after the type, or `BREAKING CHANGE:` in the body   -> major
#   feat                                                      -> minor
#   anything else                                             -> patch
#
# One exception, and it is the usual one. While the major is 0 the interface is not
# promised yet, so a breaking change moves the minor rather than declaring 1.0.0. That
# decision should be a person's, and `major` is there for when they make it.
#
# Usage: next-version.sh [auto|major|minor|patch|x.y.z]

set -euo pipefail

mode="${1:-auto}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "${mode}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    echo "${mode}"
    exit 0
fi

latest="$(git -C "${root}" tag --list 'v*' | sort --version-sort | tail -1)"
current="${latest#v}"
if [ -z "${current}" ]; then
    # No tag yet, so there is nothing to bump from and the first release is what the
    # files already say.
    "${root}/scripts/check-version.sh" > /dev/null
    python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['version'])" \
        "${root}/app/src-tauri/tauri.conf.json"
    exit 0
fi

if [ "${mode}" = "auto" ]; then
    subjects="$(git -C "${root}" log --format=%s "${latest}..HEAD")"
    bodies="$(git -C "${root}" log --format=%B "${latest}..HEAD")"
    if [ -z "${subjects}" ]; then
        echo "nothing has been committed since ${latest}" >&2
        exit 1
    fi
    if printf '%s\n' "${subjects}" | grep --quiet --extended-regexp '^[a-z]+(\([^)]*\))?!:' \
        || printf '%s\n' "${bodies}" | grep --quiet '^BREAKING CHANGE:'; then
        mode="major"
    elif printf '%s\n' "${subjects}" | grep --quiet --extended-regexp '^feat(\([^)]*\))?:'; then
        mode="minor"
    else
        mode="patch"
    fi
fi

# Split on the dots without a here-string, which needs a writable temporary file and
# does not always have one.
core="${current%%-*}"
major="${core%%.*}"
rest="${core#*.}"
minor="${rest%%.*}"
patch="${rest#*.}"

case "${mode}" in
    major)
        if [ "${major}" -eq 0 ]; then
            # Still 0.x. See the note at the top.
            echo "0.$((minor + 1)).0"
        else
            echo "$((major + 1)).0.0"
        fi
        ;;
    minor) echo "${major}.$((minor + 1)).0" ;;
    patch) echo "${major}.${minor}.$((patch + 1))" ;;
    *)
        echo "${mode} is not auto, major, minor, patch, or a version." >&2
        exit 1
        ;;
esac
