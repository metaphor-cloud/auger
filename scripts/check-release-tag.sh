#!/usr/bin/env bash
#
# Check that a tag is a release nobody has had before.
#
# check-version.sh proves the tag and the bundle agree. It cannot see that the version
# already shipped. Releasing a version twice builds a second, different bundle claiming
# to be the first, and the updater has no way to tell them apart: it compares versions,
# so two builds calling themselves 0.1.0 are the same release to every copy that already
# installed one. That is the same failure check-version.sh exists to prevent, arriving
# through another door.
#
# The tag being released is on this commit, so it is not evidence of a previous release.
# A tag for the same version on a different commit is, and that is what force-pushing a
# released tag looks like.
#
# Needs the tags. A shallow checkout has none, so the workflow fetches them.
#
# Usage: check-release-tag.sh <version without the leading v>

set -euo pipefail

want="${1:?usage: check-release-tag.sh <version>}"

if ! [[ "${want}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    echo "${want} is not a version this can release." >&2
    exit 1
fi

here="$(git tag --points-at HEAD --list 'v*' || true)"
released="$(comm -23 \
    <(git tag --list 'v*' | sort) \
    <(printf '%s\n' "${here}" | sort) | sed 's/^v//')"

if [ -z "${released}" ]; then
    echo "${want} is the first release"
    exit 0
fi

if printf '%s\n' "${released}" | grep --quiet --line-regexp --fixed-strings "${want}"; then
    echo "${want} is already released, on another commit." >&2
    exit 1
fi

newest="$(printf '%s\n' "${released}" | sort --version-sort | tail -1)"

# sort -V puts 0.2.0 after 0.1.0. The newer of the two has to be the one being released.
if [ "$(printf '%s\n%s\n' "${want}" "${newest}" | sort --version-sort | tail -1)" != "${want}" ]; then
    echo "${want} is not newer than ${newest}, which is already released." >&2
    exit 1
fi

echo "${want} comes after ${newest}"
