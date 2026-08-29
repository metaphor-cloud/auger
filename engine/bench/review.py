"""Measure how much a reviewer model actually finds.

Every case in `bench/cases` is a real defect planted in plausible code. The harness
builds a throwaway git repository, commits the clean version, commits the defective one,
and runs the same `diff_review` job a review runs. A model sees the diff and nothing
else: the answer never reaches it.

    just bench-review                       every case, the configured reviewer
    just bench-review --tier 4              only the ones that need real understanding
    just bench-review --model gpt-oss-120b  a model that is not the configured one

Two numbers come back. Detection is the share of planted defects the model reported.
Noise is how many findings it reported that were not the planted defect, per case: a
model that reports everything detects everything and is worth nothing.

A finding counts as a detection when it points inside the planted span, give or take a
few lines. Line proximity is a blunt rule and it is the honest one: asking a second model
whether two descriptions mean the same thing measures that model too.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from auger.config.loader import config_path, home_dir, load
from auger.config.schema import Config, Policy
from auger.jobs import diff_review
from auger.llm import Gateway
from auger.llm.supervisor import Supervisor
from auger.models import Repository
from auger.net import Allowlist
from auger.store.db import Store

CASES = Path(__file__).parent / "cases"

#: How far from the planted line a finding may point and still be that defect. A model
#: naming the function rather than the exact statement is not wrong.
SLACK = 6

TIER_NAMES = {
    1: "visible in the changed lines",
    2: "needs the function, or a library's contract",
    3: "needs two parts of the file at once",
    4: "needs to know why the code was written that way",
}


@dataclass
class Case:
    name: str
    title: str
    tier: int
    tags: list[str]
    file: str
    lines: tuple[int, int]
    summary: str
    directory: Path

    @classmethod
    def load(cls, directory: Path) -> Case:
        body = tomllib.loads((directory / "case.toml").read_text(encoding="utf-8"))
        first, last = body["lines"]
        return cls(
            name=directory.name,
            title=str(body["title"]),
            tier=int(body["tier"]),
            tags=list(body.get("tags", [])),
            file=str(body["file"]),
            lines=(int(first), int(last)),
            summary=str(body.get("summary", "")).strip(),
            directory=directory,
        )

    def hit_by(self, file: str, line: int | None) -> bool:
        """Whether one finding points at this defect."""
        if Path(file).name != Path(self.file).name:
            return False
        if line is None:
            # A finding that names the right file and no line is half an answer. The
            # corpus plants one defect per file, so the file alone identifies it.
            return True
        return self.lines[0] - SLACK <= line <= self.lines[1] + SLACK


@dataclass
class Result:
    case: Case
    found: bool = False
    noise: int = 0
    reported: list[str] = field(default_factory=list)
    seconds: float = 0.0
    error: str = ""


def cases(only_tier: int | None = None, only: str = "") -> list[Case]:
    found = [
        Case.load(one)
        for one in sorted(CASES.iterdir())
        if one.is_dir() and (one / "case.toml").is_file()
    ]
    if only_tier is not None:
        found = [one for one in found if one.tier == only_tier]
    if only:
        found = [one for one in found if only in one.name]
    return found


def git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(repository),
            "GIT_AUTHOR_NAME": "bench",
            "GIT_AUTHOR_EMAIL": "bench@localhost",
            "GIT_COMMITTER_NAME": "bench",
            "GIT_COMMITTER_EMAIL": "bench@localhost",
        },
    )


def build(case: Case, where: Path) -> Repository:
    """A repository whose last commit introduces exactly this defect."""
    where.mkdir(parents=True, exist_ok=True)
    git(where, "init", "--quiet", "--initial-branch", "main")
    for stage in ("before", "after"):
        source = case.directory / stage
        for entry in sorted(source.rglob("*")):
            if entry.is_file():
                target = where / entry.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, target)
        git(where, "add", "-A")
        git(where, "commit", "--quiet", "-m", f"{stage} {case.name}", "--allow-empty")
    return Repository(path=where)


async def run_case(case: Case, config: Config, gateway: Gateway, policy: Policy) -> Result:
    result = Result(case=case)
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as scratch:
        store = Store.open(Path(scratch) / "store")
        try:
            repository = build(case, Path(scratch) / "repo")
            outcome = await diff_review.review(
                store=store,
                gateway=gateway,
                repository=repository,
                policy=policy,
                target="HEAD",
                graph=None,
            )
            if outcome.run.error:
                result.error = outcome.run.error
            for finding in outcome.findings:
                result.reported.append(f"{finding.file}:{finding.line} {finding.title}")
                if case.hit_by(finding.file, finding.line):
                    result.found = True
                else:
                    result.noise += 1
        except Exception as error:  # a harness failure is not a model failure
            result.error = f"{type(error).__name__}: {error}"
        finally:
            store.close()
    result.seconds = time.monotonic() - started
    return result


def report(results: list[Result], model: str) -> None:
    """What the model did, over the cases it actually got to answer.

    A run that failed is not a miss. Counting a dead model server as a model that saw
    the defect and said nothing would report a number that means nothing.
    """
    print(f"\nreviewer: {model}")
    for tier in sorted({one.case.tier for one in results}):
        rows = [one for one in results if one.case.tier == tier]
        answered = [one for one in rows if not one.error]
        found = sum(1 for one in answered if one.found)
        noise = sum(one.noise for one in answered)
        print(f"\ntier {tier}: {TIER_NAMES.get(tier, '')}")
        print(f"  found {found} of {len(answered)}, {noise} other findings")
        for one in rows:
            mark = "failed" if one.error else ("FOUND " if one.found else "missed")
            note = f"  [{one.error}]" if one.error else ""
            print(f"  {mark} {one.case.name:26} {one.noise:2} noise {one.seconds:6.1f}s{note}")

    answered = [one for one in results if not one.error]
    failed = len(results) - len(answered)
    if not answered:
        print(f"\ntotal: nothing was measured. {failed} runs failed.")
        return
    found = sum(1 for one in answered if one.found)
    noise = sum(one.noise for one in answered)
    print(
        f"\ntotal: found {found} of {len(answered)}"
        f" ({found / len(answered):.0%}), {noise / len(answered):.1f} other findings per case"
    )
    if failed:
        print(f"{failed} runs failed and are not counted.")


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bench.review")
    parser.add_argument("--tier", type=int, default=None, help="Only this tier.")
    parser.add_argument("--only", default="", help="Only cases whose name holds this.")
    parser.add_argument("--model", default="", help="A backend name from the config.")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case.")
    arguments = parser.parse_args(argv)

    chosen = cases(arguments.tier, arguments.only)
    if not chosen:
        print("no case matched")
        return 1

    home = home_dir()
    config = load(config_path(home))
    if arguments.model:
        profile = config.profile[config.defaults.model_profile]
        profile.review = profile.review.model_copy(update={"backend": arguments.model})
    backend = config.profile[config.defaults.model_profile].review.backend
    if backend not in config.backend:
        print(f"no backend named {backend!r} in the config")
        return 1

    # The reviewer only. Starting every backend would put two large models in memory.
    wanted = {backend: config.backend[backend]}
    gateway = Gateway(config, Allowlist.from_values([one.url for one in config.backend.values()]))
    supervisor = Supervisor(home / "models")
    results: list[Result] = []
    try:
        health = await supervisor.ensure(gateway.client, wanted)
        if not health[backend].up:
            print(f"{backend} will not start: {health[backend].reason}")
            return 1
        # No hints, no tools, no second model: this measures the reviewer.
        policy = Policy(tools=[], adversary=False)
        for case in chosen:
            for _ in range(arguments.repeat):
                # A long run outlives its server: something else stops it, or it runs
                # out of memory. Without this, every case after that reads as a miss.
                if not (await supervisor.ensure(gateway.client, wanted))[backend].up:
                    print(f"{backend} stopped answering and will not start again")
                    break
                result = await run_case(case, config, gateway, policy)
                print(
                    f"{'FOUND ' if result.found else 'missed'} {case.name}"
                    f" ({result.noise} noise, {result.seconds:.0f}s)"
                )
                results.append(result)
    finally:
        await gateway.aclose()
        supervisor.stop_all()

    report(results, config.backend[backend].model or backend)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
