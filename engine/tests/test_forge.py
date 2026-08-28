"""The forge adapters run against a server that speaks the real API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest

from auger.config.schema import Config
from auger.config.schema import Forge as ForgeConfig
from auger.forge import Comment, ForgeError, NoTokenError, Registry, Repo, resolve_token
from auger.forge.github import GitHub
from auger.forge.gitlab import GitLab
from auger.models import Remote, Repository
from tests.helpers import FakeGitHub, FakeGitLab

Serve = Callable[[object], Awaitable[str]]
REPO = Repo(owner="acme", name="thing")


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


# --- GitHub ------------------------------------------------------------------------


@pytest.fixture
def hub() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
async def github(hub: FakeGitHub, serve: Serve, client: httpx.AsyncClient) -> GitHub:
    base = await serve(hub.app())
    return GitHub(client, base, "secret-token", "github.com")


async def test_it_reads_who_the_user_is(github: GitHub) -> None:
    assert await github.whoami() == "ru"


async def test_it_lists_open_pull_requests(github: GitHub, hub: FakeGitHub) -> None:
    hub.add_pull(number=7, assignees=["ru"], reviewers=["other"])
    pulls = await github.pull_requests(REPO)
    assert pulls[0].number == 7
    assert pulls[0].assignees == ("ru",)
    assert pulls[0].reviewers == ("other",)
    assert pulls[0].head_sha == "abc123"


async def test_a_pull_request_knows_whether_it_concerns_the_user(
    github: GitHub, hub: FakeGitHub
) -> None:
    hub.add_pull(number=1, assignees=["someone"])
    hub.add_pull(number=2, reviewers=["ru"])
    pulls = await github.pull_requests(REPO)
    assert pulls[0].concerns("ru") is False
    assert pulls[1].concerns("ru") is True


async def test_it_asks_for_the_diff_media_type(github: GitHub, hub: FakeGitHub) -> None:
    hub.add_pull()
    assert "+    handle = open(path)" in await github.diff(REPO, 7)


async def test_a_draft_review_is_never_submitted(github: GitHub, hub: FakeGitHub) -> None:
    """Without an event the review stays pending, and only a person submits it."""
    hub.add_pull()
    pull = (await github.pull_requests(REPO))[0]
    posted = await github.post_review(REPO, pull, "summary", [], submit=False)
    assert "event" not in hub.reviews[0]
    assert posted.submitted is False


async def test_a_complete_review_is_submitted(github: GitHub, hub: FakeGitHub) -> None:
    hub.add_pull()
    pull = (await github.pull_requests(REPO))[0]
    posted = await github.post_review(REPO, pull, "summary", [], submit=True)
    assert hub.reviews[0]["event"] == "COMMENT"
    assert posted.submitted is True


async def test_a_comment_carries_its_file_and_line(github: GitHub, hub: FakeGitHub) -> None:
    hub.add_pull()
    pull = (await github.pull_requests(REPO))[0]
    await github.post_review(
        REPO, pull, "summary", [Comment(path="reader.py", line=2, body="leak")], submit=False
    )
    assert hub.reviews[0]["comments"] == [{"path": "reader.py", "line": 2, "body": "leak"}]
    assert hub.reviews[0]["commit_id"] == "abc123"


async def test_a_comment_with_no_line_is_left_out(github: GitHub, hub: FakeGitHub) -> None:
    """GitHub refuses a line comment with no line, and one bad comment loses them all."""
    hub.add_pull()
    pull = (await github.pull_requests(REPO))[0]
    await github.post_review(
        REPO, pull, "summary", [Comment(path="reader.py", line=None, body="x")], submit=False
    )
    assert hub.reviews[0]["comments"] == []


async def test_the_token_is_sent_and_never_in_a_url(github: GitHub, hub: FakeGitHub) -> None:
    await github.whoami()
    assert hub.tokens[0] == "Bearer secret-token"


async def test_a_rate_limit_says_so_plainly(github: GitHub, hub: FakeGitHub) -> None:
    hub.rate_limited = True
    with pytest.raises(ForgeError, match="rate limit"):
        await github.whoami()


# --- GitLab ------------------------------------------------------------------------


@pytest.fixture
def lab() -> FakeGitLab:
    return FakeGitLab()


@pytest.fixture
async def gitlab(lab: FakeGitLab, serve: Serve, client: httpx.AsyncClient) -> GitLab:
    base = await serve(lab.app())
    return GitLab(client, base, "secret-token", "gitlab.com")


async def test_gitlab_reads_who_the_user_is(gitlab: GitLab, lab: FakeGitLab) -> None:
    assert await gitlab.whoami() == "ru"
    assert lab.tokens[0] == "secret-token"


async def test_gitlab_lists_merge_requests(gitlab: GitLab, lab: FakeGitLab) -> None:
    lab.add_merge_request(iid=3, reviewers=["ru"])
    pulls = await gitlab.pull_requests(REPO)
    assert pulls[0].number == 3
    assert pulls[0].concerns("ru") is True


async def test_gitlab_builds_a_patch_from_its_changes(gitlab: GitLab, lab: FakeGitLab) -> None:
    lab.add_merge_request()
    diff = await gitlab.diff(REPO, 3)
    assert "diff --git a/reader.py b/reader.py" in diff
    assert "+    handle = open(path)" in diff


async def test_gitlab_writes_draft_notes_and_does_not_publish(
    gitlab: GitLab, lab: FakeGitLab
) -> None:
    lab.add_merge_request()
    pull = (await gitlab.pull_requests(REPO))[0]
    await gitlab.post_review(REPO, pull, "summary", [Comment("reader.py", 2, "leak")], submit=False)
    assert len(lab.draft_notes) == 2
    assert lab.published is False


async def test_gitlab_publishes_when_the_mode_is_complete(gitlab: GitLab, lab: FakeGitLab) -> None:
    lab.add_merge_request()
    pull = (await gitlab.pull_requests(REPO))[0]
    await gitlab.post_review(REPO, pull, "summary", [], submit=True)
    assert lab.published is True


# --- credentials and matching -------------------------------------------------------


def test_the_token_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_FORGE_TOKEN", "from-env")
    config = ForgeConfig(token_env="TEST_FORGE_TOKEN", token_command=["false"])
    assert resolve_token(config) == "from-env"


def test_the_token_falls_back_to_the_forge_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_FORGE_TOKEN", raising=False)
    config = ForgeConfig(token_env="TEST_FORGE_TOKEN", token_command=["echo", "from-tool"])
    assert resolve_token(config) == "from-tool"


def test_no_credential_says_what_to_do(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_FORGE_TOKEN", raising=False)
    config = ForgeConfig(token_env="TEST_FORGE_TOKEN", token_command=["false"])
    with pytest.raises(NoTokenError, match="TEST_FORGE_TOKEN"):
        resolve_token(config)


async def test_a_forge_that_is_off_is_not_built(client: httpx.AsyncClient) -> None:
    registry = Registry(Config(), client)
    assert registry.entries == {}


async def test_an_enabled_forge_answers_for_its_host(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    config = Config()
    config.forge["github"].enabled = True
    registry = Registry(config, client)
    found = registry.for_repository(
        Repository(
            path=__import__("pathlib").Path("/x"), remote=Remote("github.com", "acme", "thing")
        )
    )
    assert found is not None
    assert found[1].slug == "acme/thing"


async def test_a_forge_with_no_credential_is_reported_not_hidden(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    config = Config()
    config.forge["github"].enabled = True
    config.forge["github"].token_command = ["false"]
    registry = Registry(config, client)
    assert registry.entries == {}
    assert "GITHUB_TOKEN" in registry.problems["github"]


async def test_a_repository_with_no_remote_matches_no_forge(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    config = Config()
    config.forge["github"].enabled = True
    registry = Registry(config, client)
    assert registry.for_repository(Repository(path=__import__("pathlib").Path("/x"))) is None
