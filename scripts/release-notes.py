#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["anthropic"]
# ///
"""Write the release notes for one version, from the commits it contains.

GitHub's generated notes are a list of commit subjects, which is the same
information the log already holds and in the same order. What a reader wants is
what changed for them, and the commit bodies say that: this repository writes the
reason a change was made into the body, not the diff.

Nothing here is load-bearing. A release with no notes is a release; a release that
does not build is not. So every failure prints why and falls back to the commit
subjects rather than stopping the workflow.

Credentials come from workload identity federation. The SDK exchanges the GitHub
OIDC token itself when the federation environment is set, so there is no key to
store and none to leak.

Usage: release-notes.py <version> <previous tag or empty> > notes.md
"""

from __future__ import annotations

import os
import subprocess
import sys

MODEL = "claude-opus-5"

SYSTEM = """\
You write release notes for Auger, a background code review rig that runs local \
models on a developer's own machine.

You are given the commits in one release. Write what changed for somebody who uses \
it, in the order that matters to them: what they will notice first, then the rest.

- Lead with the fact. No preamble, no "this release contains".
- A fix is worth more than a refactor. Something that was broken and now works goes \
first, and says what was broken.
- Group related commits into one line rather than listing each.
- Leave out anything a user cannot observe: internal refactors, test changes, \
tooling, version bumps. If that leaves nothing, say the release is internal \
changes only, in one line.
- Plain sentences. No headings, no bold, no emoji, no exclamation marks.
- Markdown bullets, one per change, at most eight.
- Never invent a change that is not in the commits.
"""


def run(*command: str) -> str:
    return subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=60
    ).stdout.strip()


def previous_tag(version: str) -> str:
    """The release before this one, or empty when this is the first.

    Worked out here rather than passed in, so the workflow holds no git of its own.
    The tag being released is on this commit and is not a previous release.
    """
    try:
        here = set(run("git", "tag", "--points-at", "HEAD", "--list", "v*").split())
        tags = [tag for tag in run("git", "tag", "--list", "v*").split() if tag not in here]
    except subprocess.SubprocessError:
        return ""
    if not tags:
        return ""

    def order(tag: str) -> list[int]:
        # Sorts 0.2.10 after 0.2.9, which a string comparison does not.
        parts = tag.lstrip("v").split("-", 1)[0].split(".")
        return [int(part) if part.isdigit() else 0 for part in parts]

    return max(tags, key=order)


def commits(previous: str) -> str:
    """The commits this release adds, with their bodies. The body holds the reason."""
    span = f"{previous}..HEAD" if previous else "HEAD"
    return run("git", "log", span, "--no-merges", "--pretty=format:%s%n%b%n---")


def subjects(previous: str) -> str:
    """The fallback: what GitHub would have produced on its own."""
    span = f"{previous}..HEAD" if previous else "HEAD"
    log = run("git", "log", span, "--no-merges", "--pretty=format:- %s")
    return log or "- No changes."


def notes(version: str, log: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        messages=[
            {
                "role": "user",
                "content": f"Release {version}. The commits it adds:\n\n{log}",
            }
        ],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"the model declined: {response.stop_details}")
    written = "\n".join(
        block.text for block in response.content if block.type == "text"
    ).strip()
    if not written:
        raise RuntimeError("the model returned nothing")
    return written


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else ""
    if not version:
        print("usage: release-notes.py <version> [previous tag]", file=sys.stderr)
        return 2
    previous = sys.argv[2] if len(sys.argv) > 2 else previous_tag(version)

    log = commits(previous)
    if not log.strip():
        print("- No changes.")
        return 0

    try:
        print(notes(version, log))
    except Exception as error:
        # The release still ships. Say what went wrong where the workflow log
        # keeps it, and fall back to what the commits already say.
        print(f"release notes fell back to commit subjects: {error}", file=sys.stderr)
        print(subjects(previous))
    return 0


if __name__ == "__main__":
    sys.exit(main())
