"""Measure one embedding model against another on real code retrieval.

The question an embedder has to answer is: given the body of a symbol that changed, which
other files does a reviewer need to see? So the ground truth for a symbol is the set of
files that name it, computed from the syntax tree rather than from any search the rig
runs. Using the rig's own keyword search to define the answer would score the keyword
search, not the embedder.

    uv run python -m bench.retrieval ~/git/metaphor/auger nomic-embed-code Qwen3-Embedding-8B

It indexes the repository once per model into a throwaway database, runs the same queries
against each, and prints recall at 12 and precision at 5. Nothing here touches the real
store, and nothing here runs during a review.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auger.config.schema import Backend, CodeGraph, Config, Profile, ProfileEntry
from auger.context import codegraph
from auger.context.indexer import reindex
from auger.llm import Gateway, catalog
from auger.llm.supervisor import Supervisor
from auger.net import Allowlist
from auger.store.db import Store
from auger.store.index import search_text, search_vectors

#: How many symbols to ask about. Enough to separate two models, few enough to run in
#: minutes on a machine that is also serving the model.
SYMBOLS = 40

#: A symbol shorter than this is a name like `run` or `of` that half the tree mentions,
#: and a query built from it measures nothing.
MIN_NAME = 6
MIN_BODY = 200

RECALL_AT = 12
PRECISION_AT = 5

#: A symbol has to be named by at least this many other files to have an answer, and by
#: no more than this many to have a reachable one. The upper bound is absolute, not a
#: share: recall at twelve against an answer key of ninety files cannot exceed 0.13, so a
#: share-based cap silently scores every model near zero on a large repository.
MIN_MENTIONS = 2
MAX_MENTIONS = 8

#: Below this the numbers are noise, and saying so beats printing them.
ENOUGH_CASES = 10


@dataclass
class Case:
    """One question: this symbol's body, and the files that mention it."""

    symbol: str
    path: str
    text: str
    relevant: set[str]


@dataclass
class Score:
    model: str
    recall: float = 0.0
    precision: float = 0.0
    first_rank: float = 0.0
    index_seconds: float = 0.0
    query_seconds: float = 0.0
    cases: int = 0
    #: Recall for each symbol, in the order the cases were built, so two models can be
    #: compared question by question rather than only on their averages.
    per_case: dict[str, float] = field(default_factory=dict)


def word(name: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(name)}\b")


def cases_from(store: Store, repository: Path) -> list[Case]:
    """Symbols worth asking about, with the files that name them.

    A symbol nothing else mentions has no right answer, and a symbol everything mentions
    has no wrong one. Both are dropped.
    """
    rows = store.query(
        "SELECT path, symbol, text FROM chunks WHERE repo_path = ? AND symbol != ''"
        " ORDER BY LENGTH(text) DESC",
        [str(repository)],
    )
    files = {
        str(row["path"]): str(row["text"])
        for row in store.query(
            "SELECT path, GROUP_CONCAT(text, '\n') AS text FROM chunks"
            " WHERE repo_path = ? GROUP BY path",
            [str(repository)],
        )
    }
    seen: set[str] = set()
    found: list[Case] = []
    for row in rows:
        name = str(row["symbol"]).split(" part ")[0].strip()
        body = str(row["text"])
        if name in seen or len(name) < MIN_NAME or len(body) < MIN_BODY:
            continue
        pattern = word(name)
        elsewhere = {
            path
            for path, text in files.items()
            if path != str(row["path"]) and pattern.search(text)
        }
        # One mention is a coincidence as often as a call. More than a handful is a
        # word like `Config` that says nothing about which file matters, and that no
        # ranking of twelve could cover anyway.
        if not MIN_MENTIONS <= len(elsewhere) <= MAX_MENTIONS:
            continue
        seen.add(name)
        found.append(Case(name, str(row["path"]), body, elsewhere))
        if len(found) >= SYMBOLS:
            break
    return found


def config_for(choice: catalog.Choice, home: Path, port: int) -> Config:
    return Config(
        backend={
            "bench-embed": Backend(
                url=f"http://127.0.0.1:{port}/v1",
                model=choice.name,
                managed=True,
                model_file=choice.filename,
                args=["--embedding", "--pooling", "last"],
            )
        },
        profile={"balanced": Profile(embed=ProfileEntry(backend="bench-embed"))},
    )


def rank_text(store: Store, case: Case, repository: Path) -> list[str]:
    """Keyword search, which is what a review falls back to with no model at all."""
    return files_of(search_text(store, case.symbol, repository, RECALL_AT * 4), case)


def rank_graph(store: Store, case: Case, repository: Path) -> list[str]:
    """A real call graph. It answers who calls this symbol, and nothing else."""
    found = codegraph.callers_for(CodeGraph(enabled=True), repository, [case.symbol])
    ranked: list[str] = []
    for caller in found:
        if caller.path not in ranked and caller.path != case.path:
            ranked.append(caller.path)
    return ranked


def files_of(hits: list[Any], case: Case) -> list[str]:
    """Hits as a file ranking. The reviewer is shown files, so two chunks of one file
    are one answer, and a symbol's own file is never the answer."""
    ranked: list[str] = []
    for hit in hits:
        if hit.path not in ranked and hit.path != case.path:
            ranked.append(hit.path)
    return ranked


def measure(name: str, cases: list[Case], ranked_for: Callable[[Case], list[str]]) -> Score:
    """Score one way of retrieving against the same questions as every other way."""
    score = Score(model=name, cases=len(cases))
    for case in cases:
        ranked = ranked_for(case)
        found = set(ranked[:RECALL_AT]) & case.relevant
        score.recall += len(found) / len(case.relevant)
        first_five = ranked[:PRECISION_AT]
        score.precision += (
            len([one for one in first_five if one in case.relevant]) / PRECISION_AT
            if first_five
            else 0.0
        )
        rank = next((index + 1 for index, one in enumerate(ranked) if one in case.relevant), 0)
        score.first_rank += 1.0 / rank if rank else 0.0
        score.per_case[case.symbol] = len(found) / len(case.relevant)
    if score.cases:
        score.recall /= score.cases
        score.precision /= score.cases
        score.first_rank /= score.cases
    return score


async def score_one(
    choice: catalog.Choice,
    repository: Path,
    home: Path,
    port: int,
    others: list[Score] | None = None,
) -> Score:
    """Index the repository with one model, then ask it every question.

    `others` collects the sources that need no model, scored on the same questions, so
    the comparison that matters is not only model against model.
    """
    score = Score(model=choice.name)
    config = config_for(choice, home, port)
    supervisor = Supervisor(home / "models")
    gateway = Gateway(config, Allowlist.from_values([config.backend["bench-embed"].url]))
    with tempfile.TemporaryDirectory() as scratch:
        store = Store.open(Path(scratch))
        try:
            health = await supervisor.ensure(gateway.client, config.backend)
            if not health["bench-embed"].up:
                print(f"  {choice.name}: will not start: {health['bench-embed'].reason}")
                return score

            started = time.monotonic()
            outcome = await reindex(store, gateway, repository)
            index_seconds = time.monotonic() - started
            if outcome.chunks_embedded == 0:
                print(f"  {choice.name}: embedded nothing")
                return score

            cases = cases_from(store, repository)
            if len(cases) < ENOUGH_CASES:
                print(
                    f"  {choice.name}: only {len(cases)} symbols qualified."
                    " Point this at a larger repository."
                )
            started = time.monotonic()
            vectors = {case.symbol: (await gateway.embed([case.text]))[0] for case in cases}
            score = measure(
                choice.name,
                cases,
                lambda case: files_of(
                    search_vectors(store, vectors[case.symbol], repository, RECALL_AT), case
                ),
            )
            score.index_seconds = index_seconds
            score.query_seconds = time.monotonic() - started
            if others is not None:
                others.append(
                    measure(
                        "keyword search", cases, lambda case: rank_text(store, case, repository)
                    )
                )
                others.append(
                    measure("call graph", cases, lambda case: rank_graph(store, case, repository))
                )
        finally:
            store.close()
            await gateway.aclose()
            supervisor.stop_all()

    if score.cases:
        score.recall /= score.cases
        score.precision /= score.cases
        score.first_rank /= score.cases
    return score


def compare(first: Score, second: Score) -> str:
    """The two models question by question.

    An average over forty symbols hides whether one model is better or whether it won a
    couple of questions by a lot. Counting the questions each one wins says which.
    """
    shared = sorted(set(first.per_case) & set(second.per_case))
    if not shared:
        return f"{first.model} and {second.model} answered no question in common"
    wins = sum(1 for name in shared if second.per_case[name] > first.per_case[name])
    losses = sum(1 for name in shared if second.per_case[name] < first.per_case[name])
    draws = len(shared) - wins - losses
    verdict = "no clear difference"
    if wins > losses * 2 and wins - losses >= 5:
        verdict = f"{second.model} looks better"
    elif losses > wins * 2 and losses - wins >= 5:
        verdict = f"{first.model} looks better"
    return (
        f"{second.model} against {first.model} over {len(shared)} symbols: "
        f"{wins} better, {losses} worse, {draws} the same. {verdict}."
    )


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bench.retrieval")
    parser.add_argument("repository", type=Path)
    parser.add_argument("models", nargs="+", help="Catalogue names to compare.")
    parser.add_argument("--port", type=int, default=1399)
    parser.add_argument("--home", type=Path, default=Path.home() / ".auger")
    arguments = parser.parse_args(argv)

    repository = arguments.repository.expanduser().resolve()
    scores: list[Score] = []
    # The sources that need no model are scored once, on the questions the first model
    # built, so every row in the table answered the same forty questions.
    others: list[Score] | None = []
    model_free: list[Score] = []
    for name in arguments.models:
        choice = catalog.by_name(name)
        if not catalog.downloaded(choice, arguments.home / "models"):
            print(f"{name}: not downloaded, skipping")
            continue
        print(f"{name}: indexing {repository.name}")
        scores.append(await score_one(choice, repository, arguments.home, arguments.port, others))
        if others is not None:
            model_free = others
            others = None
    scores += [one for one in model_free if one.cases]

    print(f"\n{'model':24} {'recall@12':>10} {'prec@5':>8} {'MRR':>6} {'index s':>9} {'cases':>6}")
    for score in scores:
        print(
            f"{score.model:24} {score.recall:10.3f} {score.precision:8.3f}"
            f" {score.first_rank:6.3f} {score.index_seconds:9.1f} {score.cases:6}"
        )
    if len(scores) > 1:
        print()
        for other in scores[1:]:
            print(compare(scores[0], other))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
