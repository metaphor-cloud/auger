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

from auger.config.schema import Backend, Config, JobClass
from auger.downloads import Item, Job, Manager
from auger.llm import catalog, runtime
from auger.llm.catalog import CatalogError, Choice, RepoChoice
from auger.llm.runtime import RuntimeInstallError
from auger.log import Logger, create_logger
from auger.net.download import Digest, DownloadError, client

REVIEW_BACKEND = "local-review"
VERIFY_BACKEND = "local-adversary"
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
    adversary_model: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def models_dir(home: Path) -> Path:
    return home / "models"


def plan(memory_gb: float | None = None, models_dir: Path | None = None) -> list[Choice]:
    """Which models this machine should fetch.

    No reranker. It measured markedly worse than not reranking, so fetching it by
    default would cost a download and give a worse review.
    """
    return [
        catalog.recommended_review_model(memory_gb, models_dir),
        catalog.recommended_embed_model(memory_gb, models_dir),
    ]


#: The port each job class gets when the rig starts a server for it. They are next to
#: each other so a machine running all three is easy to read in `lsof`.
PORTS: dict[str, int] = {"review": 1337, "embed": 1338, "rerank": 1339, "verify": 1340}
#: Ports for the second engine's servers. Its own, because both engines can be running
#: at once and two servers on one port is neither of them.
COLI_PORTS: dict[str, int] = {"review": 1345, "verify": 1346, "triage": 1347}


def backend_for(config: Config, choice: Choice, job_class: JobClass) -> str:
    """Point one job class at a model, and give it a server of its own.

    This is what a fetch does after the bytes land: the file is on disk, and something
    has to say which job it answers.
    """
    name = f"local-{job_class.value}"
    port = PORTS.get(job_class.value, 1341)
    existing = config.backend.get(name) or Backend(url=f"http://127.0.0.1:{port}/v1")
    extra = ["--embedding", "--pooling", "last"] if job_class is JobClass.EMBED else []
    config.backend[name] = existing.model_copy(
        update={
            "managed": True,
            "model": choice.name,
            "model_file": choice.filename,
            "model_url": choice.url,
            "args": extra or list(existing.args),
        }
    )
    for profile in config.profile.values():
        entry = profile.entry(job_class)
        profile_field = job_class.value
        setattr(profile, profile_field, entry.model_copy(update={"backend": name}))
    return name


def coli_backend_for(config: Config, choice: RepoChoice, job_class: JobClass) -> str:
    """Point one job class at a directory of weights served by the second engine.

    A server of its own, on its own port, because both engines can be up at once: this
    one answers the reviews while the other keeps embedding, which is the only
    arrangement that works given it has no embeddings endpoint.
    """
    name = f"coli-{job_class.value}"
    port = COLI_PORTS.get(job_class.value, 1345)
    existing = config.backend.get(name) or Backend(url=f"http://127.0.0.1:{port}/v1")
    config.backend[name] = existing.model_copy(
        update={
            "managed": True,
            "engine": "coli",
            "model": choice.name,
            # This engine reads a directory, so the name is a directory under the
            # models directory rather than a file in it.
            "model_file": choice.name,
            "model_url": f"https://huggingface.co/{choice.repo}",
            # It serves one generation at a time and queues the rest, so promising more
            # than one slot only moves the queue somewhere the rig cannot see it.
            "max_concurrent": 1,
        }
    )
    for profile in config.profile.values():
        entry = profile.entry(job_class)
        setattr(profile, job_class.value, entry.model_copy(update={"backend": name}))
    return name


def apply_to_verify(config: Config, adversary: Choice) -> Config:
    """Point the verify class at a second server, so a second model can argue.

    It gets a port of its own, because both models are up at once: one reviews while
    the other judges, and swapping them between runs needs both loaded.
    """
    config.backend[VERIFY_BACKEND] = (
        config.backend.get(VERIFY_BACKEND) or Backend(url="http://127.0.0.1:1340/v1")
    ).model_copy(
        update={
            "managed": True,
            "model": adversary.name,
            "model_file": adversary.filename,
            "model_url": adversary.url,
            "max_concurrent": 2,
        }
    )
    for profile in config.profile.values():
        if not profile.verify.backend:
            profile.verify = profile.verify.model_copy(update={"backend": VERIFY_BACKEND})
    return config


def apply_to_config(
    config: Config,
    review: Choice,
    embed: Choice,
    rerank: Choice | None = None,
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
            config.backend.get(RERANK_BACKEND) or Backend(url="http://127.0.0.1:1339/v1")
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
    adversary_model: str | None = None,
    on_step: Callable[[Step], None] | None = None,
    log: Logger | None = None,
    token: str | None = None,
    downloads: Manager | None = None,
) -> SetupResult:
    """Fetch the runtime and the weights, and write the config. Never raises."""
    log = (log or create_logger("llm")).bind(component="setup")
    result = SetupResult()
    # A first run has no rig behind it in one of the tests, so the queue is optional and
    # a local one is made when nobody handed one over.
    queue = downloads if downloads is not None else Manager(home, log=log)

    def report(step: Step) -> None:
        if on_step:
            on_step(step)

    def moving(stage: str) -> Callable[[Job], None]:
        """A job, as the first-run flow's own progress line."""

        def said(job: Job) -> None:
            report(Step(stage, job.label, job.received_bytes, job.total_bytes))

        return said

    here = models_dir(home)
    review = (
        catalog.by_name(review_model)
        if review_model
        else catalog.recommended_review_model(None, here)
    )
    embed = (
        catalog.by_name(embed_model) if embed_model else catalog.recommended_embed_model(None, here)
    )
    adversary = catalog.by_name(adversary_model) if adversary_model else None

    async with client() as http:
        try:
            report(Step("runtime", message="Looking for a model runtime"))
            existing = runtime.resolve(home)
            if existing is None:
                existing = await runtime.install(http, home, queue, moving("runtime"), log)
            result.runtime_path = str(existing)
            report(Step("runtime", message=f"Runtime ready: {existing.name}"))

            wanted = [review, embed]
            if adversary is not None:
                wanted.append(adversary)
            for choice in wanted:
                report(Step("model", choice.name, message=f"Looking up {choice.name}"))
                resolved = await catalog.resolve(http, choice, log, token)
                job = queue.submit(
                    choice.name,
                    "weights",
                    models_dir(home),
                    [
                        Item(
                            choice.filename,
                            resolved.url,
                            Digest.sha256(resolved.sha256),
                            resolved.size_bytes,
                        )
                    ],
                    watcher=moving("model"),
                )
                done = await queue.wait(job.id)
                if done is None or done.state != "done":
                    raise DownloadError(
                        (done.error if done else "") or f"{choice.name} was {job.state}"
                    )
        except (RuntimeInstallError, CatalogError, DownloadError) as error:
            log.error("setup failed", reason="setup_failed", error=error)
            result.error = str(error)
            report(Step("failed", message=str(error)))
            return result

    apply_to_config(config, review, embed)
    if adversary is not None:
        apply_to_verify(config, adversary)
        result.adversary_model = adversary.name
    result.review_model = review.name
    result.embed_model = embed.name
    report(Step("done", message=f"Ready: {review.name}"))
    log.info("setup finished", review=review.name, embed=embed.name)
    return result
