"""A diff hides what the changed code is part of and who calls it."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

import pytest

from auger.config.schema import Backend, Config, ProfileEntry
from auger.context import reindex
from auger.context.retrieve import changed_ranges, context_for_diff, symbol_names
from auger.llm import Gateway
from auger.net import Allowlist
from auger.store import Store
from auger.store.index import Hit
from auger.watch import git
from tests.helpers import FakeModelServer, git_commit, git_init

Serve = Callable[[object], Awaitable[str]]

READER = "def read(path):\n    handle = open(path)\n    return handle.read()\n"
WRITER = "def write(path, body):\n    read(path)\n    return body\n"
UNRELATED = "def colours():\n    return ['red', 'green']\n"

DIFF = """\
diff --git a/reader.py b/reader.py
index 1111111..2222222 100644
--- a/reader.py
+++ b/reader.py
@@ -1,3 +1,3 @@
 def read(path):
-    handle = open(path)
+    handle = open(path, encoding="utf-8")
     return handle.read()
"""


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    store = Store.open(tmp_path / "db")
    yield store
    store.close()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    path = git_init(tmp_path / "repo")
    git_commit(
        path,
        {"reader.py": READER, "writer.py": WRITER, "colours.py": UNRELATED},
        "start",
    )
    return path


@pytest.fixture
def fake() -> FakeModelServer:
    return FakeModelServer()


@pytest.fixture
async def gateway(fake: FakeModelServer, serve: Serve) -> AsyncIterator[Gateway]:
    base = await serve(fake.app())
    config = Config(
        backend={
            "embed": Backend(url=f"{base}/v1", model="embed-model"),
            "rerank": Backend(url=f"{base}/v1", model="rerank-model"),
        }
    )
    config.profile["balanced"].embed = ProfileEntry(backend="embed")
    config.profile["balanced"].rerank = ProfileEntry(backend="rerank")
    gateway = Gateway(config, Allowlist.from_values([base]))
    yield gateway
    await gateway.aclose()


def test_it_reads_the_changed_lines_of_a_diff() -> None:
    changes = changed_ranges(DIFF)
    assert [change.path for change in changes] == ["reader.py"]
    assert changes[0].ranges == [(1, 3)]


def test_a_diff_that_adds_a_file_still_names_it() -> None:
    diff = "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+x = 1\n"
    assert [change.path for change in changed_ranges(diff)] == ["new.py"]


def test_a_deleted_file_is_not_searched_for() -> None:
    diff = "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x = 1\n"
    assert changed_ranges(diff) == []


def test_symbol_names_take_the_leaf() -> None:
    hits = [Hit(1, "a.py", "Gateway.complete", 1, 2, ""), Hit(2, "a.py", "read part 2", 3, 4, "")]
    assert symbol_names(hits) == ["complete", "read"]


async def test_it_finds_the_caller_of_a_changed_function(store: Store, repository: Path) -> None:
    """`write` calls `read`. A reviewer needs to see that."""
    await reindex(store, None, repository)
    context = await context_for_diff(store, None, str(repository), DIFF)
    assert "read" in context.symbols
    assert "writer.py" in {hit.path for hit in context.hits}


async def test_it_does_not_return_the_changed_code_itself(store: Store, repository: Path) -> None:
    """That code is already in the diff. Related code is what the diff is missing."""
    await reindex(store, None, repository)
    context = await context_for_diff(store, None, str(repository), DIFF)
    assert all(hit.path != "reader.py" for hit in context.hits)


async def test_a_diff_that_matches_nothing_returns_nothing(store: Store, repository: Path) -> None:
    await reindex(store, None, repository)
    assert await context_for_diff(store, None, str(repository), "") == context_empty()


def context_empty() -> object:
    from auger.context.retrieve import ReviewContext

    return ReviewContext()


async def test_it_uses_the_reranker_when_there_is_a_choice(
    store: Store, repository: Path, gateway: Gateway, fake: FakeModelServer
) -> None:
    await reindex(store, gateway, repository)
    context = await context_for_diff(store, gateway, str(repository), DIFF, limit=1)
    paths = [request["path"] for request in fake.requests]
    assert "/v1/embeddings" in paths
    assert context.hits


async def test_retrieval_still_works_when_the_embedding_model_is_down(
    store: Store, repository: Path, gateway: Gateway
) -> None:
    """Keyword search alone must still find the caller."""
    await reindex(store, None, repository)
    gateway.config.backend["embed"].url = "http://127.0.0.1:1/v1"
    gateway.config.egress.allow.append("http://127.0.0.1:1")
    context = await context_for_diff(store, gateway, str(repository), DIFF)
    assert "writer.py" in {hit.path for hit in context.hits}


async def test_the_context_text_respects_its_budget(store: Store, repository: Path) -> None:
    await reindex(store, None, repository)
    context = await context_for_diff(store, None, str(repository), DIFF)
    assert len(context.as_text(budget=40)) <= 40


async def test_the_review_prompt_carries_the_related_code(store: Store, repository: Path) -> None:
    await reindex(store, None, repository)
    context = await context_for_diff(store, None, str(repository), DIFF)
    text = context.as_text()
    assert "writer.py" in text
    assert "read(path)" in text


async def test_an_index_that_is_empty_returns_nothing(store: Store, repository: Path) -> None:
    context = await context_for_diff(store, None, str(repository), DIFF)
    assert context.hits == []


async def test_a_real_repository_diff_finds_related_code(store: Store) -> None:
    """Against this repository, not a fixture."""
    here = Path(__file__).resolve().parents[2]
    if not (here / ".git").exists():
        pytest.skip("not running inside a checkout")
    await reindex(store, None, here)
    diff = git.diff(here, None, "HEAD")
    context = await context_for_diff(store, None, str(here), diff)
    assert isinstance(context.hits, list)


# --- how the two searches are combined ------------------------------------------------


def test_a_chunk_both_searches_like_beats_one_only_a_single_search_likes() -> None:
    """This is the whole reason for rank fusion."""
    from auger.context.retrieve import merge

    both = Hit(1, "both.py", "both", 1, 2, "")
    keyword_only = Hit(2, "keyword.py", "keyword", 1, 2, "")
    vector_only = Hit(3, "vector.py", "vector", 1, 2, "")
    merged = merge([[keyword_only, both], [vector_only, both]])
    assert merged[0].chunk_id == 1


def test_the_score_scales_of_the_two_searches_do_not_decide_the_order() -> None:
    """Keyword scores sit near 0.1 and vector scores near 0.8.

    Combining them by score let the vector list win every time whatever its quality.
    Measured on this repository, that cut recall from 0.58 to 0.30.
    """
    from auger.context.retrieve import merge

    strong_keyword = Hit(1, "a.py", "a", 1, 2, "", score=0.05)
    weak_vector = Hit(2, "b.py", "b", 1, 2, "", score=0.95)
    merged = merge([[strong_keyword], [weak_vector]])
    assert merged[0].chunk_id == 1


def test_an_excluded_chunk_stays_out_of_the_merge() -> None:
    from auger.context.retrieve import merge

    hit = Hit(1, "a.py", "a", 1, 2, "")
    assert merge([[hit]], exclude={1}) == []


def test_the_reranker_is_asked_a_question_not_given_a_diff() -> None:
    """A cross encoder is trained on a question and a document.

    Passing the raw diff as the query measured far worse than not reranking at all:
    recall fell from 0.686 to 0.337 on this repository.
    """
    from auger.context.retrieve import rerank_query

    query = rerank_query(["complete", "resolve"])
    assert "complete" in query
    assert "resolve" in query
    assert "diff --git" not in query
    assert query.startswith("code that")


def test_a_change_with_no_named_symbol_still_asks_something() -> None:
    from auger.context.retrieve import rerank_query

    assert rerank_query([]) == "code related to this change"
    assert rerank_query(["ab"]) == "code related to this change"
