from __future__ import annotations

from pathlib import Path

from auger.config.schema import Root
from auger.discovery import scan
from auger.discovery.walk import find_repositories
from tests.helpers import make_repo


def paths(root: Root) -> list[Path]:
    return [repository.path for repository in find_repositories(root)]


def test_it_finds_a_repository(tmp_path: Path) -> None:
    make_repo(tmp_path / "a")
    make_repo(tmp_path / "nested" / "b")
    assert paths(Root(path=tmp_path)) == [tmp_path / "a", tmp_path / "nested" / "b"]


def test_the_root_itself_can_be_a_repository(tmp_path: Path) -> None:
    make_repo(tmp_path)
    assert paths(Root(path=tmp_path)) == [tmp_path]


def test_it_does_not_descend_into_a_repository(tmp_path: Path) -> None:
    """A nested checkout belongs to its parent's review, not to its own."""
    make_repo(tmp_path / "outer")
    make_repo(tmp_path / "outer" / "inner")
    assert paths(Root(path=tmp_path)) == [tmp_path / "outer"]


def test_it_reads_the_remote(tmp_path: Path) -> None:
    make_repo(tmp_path / "a")
    found = next(iter(find_repositories(Root(path=tmp_path))))
    assert found.org_key == "github.com/acme"
    assert found.slug == "github.com/acme/thing"


def test_a_repository_with_no_remote_falls_back_to_its_path(tmp_path: Path) -> None:
    make_repo(tmp_path / "a", remote=None)
    found = next(iter(find_repositories(Root(path=tmp_path))))
    assert found.org_key is None
    assert found.slug == str(tmp_path / "a")


def test_an_exclusion_hides_a_repository(tmp_path: Path) -> None:
    make_repo(tmp_path / "keep")
    make_repo(tmp_path / "archive" / "old")
    root = Root(path=tmp_path, exclude=["archive/"])
    assert paths(root) == [tmp_path / "keep"]


def test_an_absolute_exclusion_works(tmp_path: Path) -> None:
    make_repo(tmp_path / "keep")
    make_repo(tmp_path / "archive" / "old")
    root = Root(path=tmp_path, exclude=[f"{tmp_path}/archive/"])
    assert paths(root) == [tmp_path / "keep"]


def test_an_absolute_exclusion_outside_the_root_is_ignored(tmp_path: Path) -> None:
    make_repo(tmp_path / "keep")
    root = Root(path=tmp_path, exclude=["/somewhere/else/"])
    assert paths(root) == [tmp_path / "keep"]


def test_dependency_directories_are_excluded_by_default(tmp_path: Path) -> None:
    """A vendored checkout under node_modules is not the user's code."""
    make_repo(tmp_path / "keep")
    make_repo(tmp_path / "keep-me-out" / "node_modules" / "dep")
    assert paths(Root(path=tmp_path)) == [tmp_path / "keep"]


def test_max_depth_stops_the_walk(tmp_path: Path) -> None:
    make_repo(tmp_path / "shallow")
    make_repo(tmp_path / "one" / "two" / "deep")
    assert paths(Root(path=tmp_path, max_depth=1)) == [tmp_path / "shallow"]


def test_a_missing_root_yields_nothing(tmp_path: Path) -> None:
    assert paths(Root(path=tmp_path / "gone")) == []


def test_a_repository_under_two_roots_appears_once(tmp_path: Path) -> None:
    make_repo(tmp_path / "shared" / "a")
    roots = [Root(path=tmp_path), Root(path=tmp_path / "shared")]
    assert [repository.path for repository in scan(roots)] == [tmp_path / "shared" / "a"]


def test_a_worktree_is_its_own_repository(tmp_path: Path) -> None:
    main = make_repo(tmp_path / "main")
    worktree_git = main / ".git" / "worktrees" / "feature"
    worktree_git.mkdir(parents=True)
    (worktree_git / "commondir").write_text("../..\n", encoding="utf-8")
    linked = tmp_path / "feature"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {worktree_git}\n", encoding="utf-8")

    found = {repository.path: repository for repository in find_repositories(Root(path=tmp_path))}
    assert set(found) == {main, linked}
    assert found[linked].org_key == "github.com/acme"
