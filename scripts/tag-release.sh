#!/usr/bin/env bash
#
# Commit the version bump and tag it.
#
# The tag is annotated, not light. `git describe` and the release page both read the
# message, and a light tag leaves them with the subject of whatever commit it points at.
#
# Usage: tag-release.sh <version without the leading v>

set -euo pipefail

want="${1:?usage: tag-release.sh <version>}"

if ! [[ "${want}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    echo "${want} is not a version. Use x.y.z, with no leading v." >&2
    exit 1
fi

# A retry after a build that failed arrives here with the version already written and
# the tag already on this commit. There is nothing to do, and nothing wrong.
if git rev-parse --verify --quiet "refs/tags/v${want}" > /dev/null \
    && [ "$(git rev-parse "v${want}^{commit}")" = "$(git rev-parse HEAD)" ]; then
    echo "v${want} is already on this commit"
    exit 0
fi

if [ -z "$(git status --porcelain)" ]; then
    echo "the version is already ${want} and no tag names this commit." >&2
    echo "Pass an explicit version, or tag this commit by hand." >&2
    exit 1
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

git add --all
git commit --message "chore(release): ${want}"
git tag --annotate "v${want}" --message "auger ${want}"
# The commit and the tag go together. A tag that arrives without its commit points at
# nothing anyone else can fetch.
git push --follow-tags origin HEAD

echo "tagged v${want}"
