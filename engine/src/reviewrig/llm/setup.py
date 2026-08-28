"""Getting the rig ready to work, with nothing installed first.

Point the rig at your code and it works. That means it brings its own model runtime and
its own weights, picks a model that fits the machine, writes the config, and starts the
servers.

Every step reports what it is doing, because the weights are tens of gigabytes and a
silent hour is indistinguishable from a hang.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from reviewrig.config.schema import Backend, Config
from reviewrig.llm import catalog, runtime
from reviewrig.llm.catalog import CatalogError, Choice
from reviewrig.llm.runtime import RuntimeInstallError
from reviewrig.log import Logger, create_logger
from reviewrig.net.download import DownloadError, Progress, client

REVIEW_BACKEND = "local-review"
EMBED_BACKEND = "local-embed"
RERANK_BACKEND = "local-rerank"


@dataclass
class Step:
    """What the setup is doing now. The UI shows this."""

    stage: str
    name: str = ""
    received_bytes: int = 0
    total_bytes: int = 0
    message: str = ""

    @property
    def fraction(self) -> float:
        return self.received_bytes / self.total_bytes if self.total_bytes else 0.0


@dataclass
class SetupResult:
    runtime_path: str = ""
    review_model: str = ""
    embed_model: str = ""
    rerank_model: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def models_dir(home: Path) -> Path:
    return home / "models"


def plan(memory_gb: float | None = None) -> list[Choice]:
    """Which models this machine should fetch."""
    return [
        catalog.recommended_review_model(memory_gb),
        catalog.recommended_embed_model(memory_gb),
        catalog.RERANK_MODEL,
    ]


def apply_to_config(
    config: Config, review: Choice, embed: Choice, rerank: Choice | None = None
) -> Config:
    """Point the managed backends at the files that were fetched."""
    config.backend[REVIEW_BACKEND] = (config.backend.get(REVIEW_BACKEND) or Backend()).model_copy(
        update={
            "managed": True,
            "model": review.name,
            "model_file": review.filename,
            "model_url": review.url,
        }
    )
    config.backend[EMBED_BACKEND] = (config.backend.get(EMBED_BACKEND) or Backend()).model_copy(
        update={
            "managed": True,
            "model": embed.name,
            "model_file": embed.filename,
            "model_url": embed.url,
            "args": ["--embedding", "--pooling", "last"],
        }
    )
    if rerank is not None:
        config.backend[RERANK_BACKEND] = (
            config.backend.get(RERANK_BACKEND) or Backend(url="http://127.0.0.1:8082/v1")
        ).model_copy(
            update={
                "managed": True,
                "model": rerank.name,
                "model_file": rerank.filename,
                "model_url": rerank.url,
                "args": ["--reranking"],
            }
        )
        # Reranking is off in the built-in profile. Fetching it is what turns it on.
        for profile in config.profile.values():
            if not profile.rerank.backend:
                profile.rerank = profile.rerank.model_copy(update={"backend": RERANK_BACKEND})
    return config


async def install(
    home: Path,
    config: Config,
    review_model: str | None = None,
    embed_model: str | None = None,
    on_step: Callable[[Step], None] | None = None,
    log: Logger | None = None,
) -> SetupResult:
    """Fetch the runtime and the weights, and write the config. Never raises."""
    log = (log or create_logger("llm")).bind(component="setup")
    result = SetupResult()

    def report(step: Step) -> None:
        if on_step:
            on_step(step)

    review = catalog.by_name(review_model) if review_model else catalog.recommended_review_model()
    embed = catalog.by_name(embed_model) if embed_model else catalog.recommended_embed_model()
    rerank = catalog.RERANK_MODEL

    async with client() as http:
        try:
            report(Step("runtime", message="Looking for a model runtime"))
            existing = runtime.resolve(home)
            if existing is None:

                def runtime_progress(progress: Progress) -> None:
                    report(
                        Step(
                            "runtime",
                            progress.name,
                            progress.received_bytes,
                            progress.total_bytes,
                        )
                    )

                existing = await runtime.install(http, home, runtime_progress, log)
            result.runtime_path = str(existing)
            report(Step("runtime", message=f"Runtime ready: {existing.name}"))

            for choice in (review, embed, rerank):
                report(Step("model", choice.name, message=f"Looking up {choice.name}"))
                resolved = await catalog.resolve(http, choice, log)

                def model_progress(progress: Progress) -> None:
                    report(
                        Step(
                            "model",
                            progress.name,
                            progress.received_bytes,
                            progress.total_bytes,
                        )
                    )

                from reviewrig.net.download import fetch

                await fetch(
                    http,
                    resolved.url,
                    models_dir(home) / choice.filename,
                    resolved.sha256,
                    model_progress,
                    log,
                )
        except (RuntimeInstallError, CatalogError, DownloadError) as error:
            log.error("setup failed", reason="setup_failed", error=error)
            result.error = str(error)
            report(Step("failed", message=str(error)))
            return result

    apply_to_config(config, review, embed, rerank)
    result.review_model = review.name
    result.embed_model = embed.name
    result.rerank_model = rerank.name
    report(Step("done", message=f"Ready: {review.name}"))
    log.info("setup finished", review=review.name, embed=embed.name)
    return result
