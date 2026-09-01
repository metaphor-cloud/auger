"""Find or start the model servers.

The rig prefers a server that the user already runs, because that server holds the model
the user chose and it may already be warm. It starts one of its own only when nothing
answers, and it says plainly why when it cannot.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from auger.config.schema import Backend
from auger.llm import sizing
from auger.log import Logger, create_logger
from auger.sandbox.which import find

PROBE_TIMEOUT = 2.0
START_TIMEOUT = 180.0
POLL_SECONDS = 1.0
#: Where an OpenAI-compatible server usually listens. `llama-server` and
#: `mlx-openai-server` both default into this range.
#: How long a server gets to stop politely before it is ended. The host waits a little
#: longer than this before it ends the engine, so the servers go first.
ADOPT_TIMEOUT = 5.0
STOP_TIMEOUT = 5.0

COMMON_PORTS = (1337, 1338, 1339, 8080, 8081, 8082, 8083, 1234, 11434, 8000)
SERVERS = ("llama-server", "mlx_lm.server", "mlx-openai-server")


@dataclass(frozen=True)
class Health:
    name: str
    url: str
    up: bool
    models: tuple[str, ...] = ()
    reason: str | None = None
    managed: bool = False

    @property
    def serves_wanted_model(self) -> bool:
        return self.up


@dataclass
class Managed:
    """A server process that the rig started and must stop again."""

    name: str
    process: subprocess.Popen[bytes]
    port: int

    def signal(self) -> None:
        """Ask it to stop. It may take a moment to let go of the weights."""
        if self.process.poll() is None:
            self.process.terminate()

    def wait(self, timeout: float = STOP_TIMEOUT) -> None:
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def stop(self) -> None:
        self.signal()
        self.wait()


def port_of(url: str) -> int:
    parts = urlsplit(url)
    return parts.port or (443 if parts.scheme == "https" else 80)


def base_of(url: str) -> str:
    """`http://host:port/v1/` becomes `http://host:port/v1`."""
    return url.rstrip("/")


async def probe(client: httpx.AsyncClient, name: str, backend: Backend) -> Health:
    """Ask a backend which models it serves."""
    url = f"{base_of(backend.url)}/models"
    try:
        response = await client.get(url, timeout=PROBE_TIMEOUT)
        response.raise_for_status()
        body = response.json()
    except Exception as error:
        return Health(name=name, url=backend.url, up=False, reason=str(error))
    models = tuple(str(row.get("id", "")) for row in body.get("data", []) if row.get("id"))
    return Health(name=name, url=backend.url, up=True, models=models)


async def probe_all(client: httpx.AsyncClient, backends: dict[str, Backend]) -> dict[str, Health]:
    names = list(backends)
    results = await asyncio.gather(
        *(probe(client, name, backends[name]) for name in names), return_exceptions=False
    )
    return dict(zip(names, results, strict=True))


async def discover(client: httpx.AsyncClient, ports: Iterable[int] = COMMON_PORTS) -> list[Health]:
    """Find an OpenAI-compatible server that the user already runs."""
    candidates = {f"port-{port}": Backend(url=f"http://127.0.0.1:{port}/v1") for port in ports}
    found = await probe_all(client, candidates)
    return [health for health in found.values() if health.up]


class Supervisor:
    """Starts a `llama-server` for any managed backend that does not answer."""

    def __init__(
        self, models_dir: Path, log: Logger | None = None, log_dir: Path | None = None
    ) -> None:
        self.models_dir = models_dir
        # Where each server's output goes. Named rather than derived from `models_dir`,
        # because deriving it puts two supervisors with different model directories in
        # the same place, and the second truncates the first one's log.
        self.log_dir = log_dir if log_dir is not None else models_dir / "logs"
        self.log = (log or create_logger("llm")).bind(component="supervisor")
        self.running: dict[str, Managed] = {}
        #: The context each managed server was actually started with. A backend that
        #: works its context out has no number in the config, so this is the only place
        #: that knows how much room a prompt really has.
        self.contexts: dict[str, int] = {}

    def server_command(self, engine: str = "llama") -> str | None:
        """The server to run: one the user has, or the one the rig installed itself.

        A graphical application has a narrow PATH, and a first run has nothing installed
        at all, so neither `PATH` alone nor `which` alone is enough.

        The second engine is never on `PATH` and is never a server the user already
        runs: it is a launcher the rig unpacked, so only its own directory is looked in.
        """
        from auger.llm import coli, runtime

        if engine == coli.NAME:
            launcher = coli.installed(self.home())
            return str(launcher) if launcher is not None else None
        own = runtime.resolve(self.models_dir.parent)
        if own is not None:
            return str(own)
        for candidate in SERVERS:
            found = find(candidate)
            if found:
                return found
        return None

    def model_path(self, backend: Backend) -> Path | None:
        return self.models_dir / backend.model_file if backend.model_file else None

    def home(self) -> Path:
        """The auger home. Everything the rig installed lives under it."""
        return self.models_dir.parent

    def wait_for(self, pid: int, timeout: float = ADOPT_TIMEOUT) -> bool:
        """Wait for a process to go, and insist when it will not.

        A server that ignores the polite signal still holds the memory, so waiting
        politely for ever is the same as never stopping it at all. It holds no state
        that a kill could spoil.
        """
        import psutil

        try:
            psutil.Process(pid).wait(timeout=timeout)
        except psutil.TimeoutExpired:
            with contextlib.suppress(OSError, psutil.Error):
                os.kill(pid, signal.SIGKILL)
            self.log.warn("server ignored the stop signal", reason="killed", pid=pid)
        except psutil.NoSuchProcess:
            return True
        except (OSError, psutil.Error) as error:
            self.log.warn("could not stop a server", reason="stop_failed", pid=pid, error=error)
            return False
        return True

    def end(self, pid: int) -> bool:
        """Ask one server to stop, and wait for it."""
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return True
        except OSError as error:
            self.log.warn("could not stop a server", reason="stop_failed", pid=pid, error=error)
            return False
        return self.wait_for(pid)

    def adopt_all(self) -> list[int]:
        """Every model server that came out of the auger home, on any port.

        A config change moves a backend's port, and a server started before the change
        keeps the old one. Unload all has to mean all of them, or the memory only comes
        back on a restart.
        """
        import psutil

        mine = str(self.home())
        found: list[int] = []
        for process in psutil.process_iter(["exe"]):
            try:
                if (process.info["exe"] or "").startswith(mine):
                    found.append(int(process.pid))
            except psutil.Error:
                continue
        return found

    def adopt(self, backend: Backend) -> int | None:
        """The process id of a server on this backend's port that auger left behind.

        A quit, a crash, or a restart leaves a model server holding tens of gigabytes
        with nothing to stop it. The window has to be able to end it, so the rig looks
        for one on the backend's port.

        Only a process started from the auger home counts. A `llama-server` the user
        runs themselves, from their own build, is theirs, and the rig never ends it.
        """
        import psutil

        wanted = str(port_of(backend.url))
        mine = str(self.home())
        for process in psutil.process_iter(["exe", "cmdline"]):
            try:
                exe = process.info["exe"] or ""
                command = process.info["cmdline"] or []
                if not exe.startswith(mine):
                    continue
                if "--port" not in command:
                    continue
                if command[command.index("--port") + 1] != wanted:
                    continue
            except (psutil.Error, IndexError, ValueError):
                continue
            return int(process.pid)
        return None

    def log_file(self, name: str) -> Path:
        """Where one managed server's output goes. One file per backend, per start."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        return self.log_dir / f"{name}.log"

    def last_output(self, name: str, lines: int = 20) -> str:
        """The tail of what a server said. Empty when it said nothing, or wrote nothing."""
        path = self.log_file(name)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return "\n".join(text.strip().splitlines()[-lines:])

    def context_for(self, name: str, backend: Backend, model_path: Path, others: list[Path]) -> int:
        """How large a context this backend gets, and why, said once in the log.

        A number in the config is honoured but still held to what the model was trained
        for and what the machine can hold. Zero means work it out.
        """
        from auger.llm import coli

        if backend.engine == coli.NAME:
            # This engine's weights are a directory of shards with no header to read a
            # trained context out of, and it streams them rather than holding them, so
            # the memory arithmetic that sizes the other engine does not apply either.
            chosen = backend.context_tokens or sizing.MINIMUM_CONTEXT * 4
            self.contexts[name] = chosen
            return chosen
        model = sizing.read(model_path, self.log)
        if model is None:
            # Nothing to reason from. Fall back to a size that fits any machine rather
            # than letting the server take the model's whole training context.
            chosen = backend.context_tokens or sizing.MINIMUM_CONTEXT * 4
            self.log.warn(
                "context not worked out",
                reason="no_model_header",
                context=chosen,
                path=str(model_path),
            )
            self.contexts[name] = chosen
            return chosen

        allowance = sizing.budget([model, *(m for m in map(self._read, others) if m)])
        if backend.context_tokens:
            chosen, moved = sizing.clamp(
                backend.context_tokens, model, backend.max_concurrent, allowance
            )
            if moved:
                self.log.warn(
                    "configured context reduced",
                    reason="context_clamped",
                    asked=backend.context_tokens,
                    context=chosen,
                    detail=moved,
                )
            self.contexts[name] = chosen
            return chosen

        # An embedding server says so in its own arguments, and one chunk is all it
        # ever reads.
        ceiling = sizing.EMBEDDING_CONTEXT if "--embedding" in backend.args else 0
        chosen = (
            sizing.choose(model, backend.max_concurrent, allowance, ceiling)
            or sizing.MINIMUM_CONTEXT
        )
        self.log.info(
            "context worked out",
            context=chosen,
            trained=model.context_length,
            slots=backend.max_concurrent,
            cache_gb=round(model.cache_bytes(chosen, backend.max_concurrent) / 2**30, 1),
        )
        self.contexts[name] = chosen
        return chosen

    def _read(self, path: Path) -> sizing.Model | None:
        return sizing.read(path, self.log) if path.exists() else None

    def other_models(self, name: str, config: dict[str, Backend]) -> list[Path]:
        """Every other managed model's weights. They are resident too, or will be."""
        found = []
        for other, backend in config.items():
            if other == name or not backend.managed:
                continue
            path = self.model_path(backend)
            if path is not None and path.exists():
                found.append(path)
        return found

    def arguments(
        self, backend: Backend, server: str, model_path: Path, context: int = 0
    ) -> list[str]:
        from auger.llm import coli

        if backend.engine == coli.NAME:
            # A different engine, so a different command line: a directory of weights
            # rather than a file, a slot count rather than a shared context size, and
            # the launcher run through the interpreter it is written for.
            interpreter = coli.python()
            room = context or backend.context_tokens or sizing.MINIMUM_CONTEXT
            return [
                *([interpreter] if interpreter else []),
                server,
                "serve",
                "--model",
                str(model_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(port_of(backend.url)),
                # One request at a time per slot, as with the other engine.
                "--kv-slots",
                str(backend.max_concurrent),
                # Its own default ceiling on an answer is a thousand tokens, which cuts
                # a review's findings off part way. The reply is what a review is.
                "--max-tokens",
                str(max(room, sizing.MINIMUM_CONTEXT)),
                *backend.args,
            ]
        return [
            server,
            "--model",
            str(model_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port_of(backend.url)),
            # A continuous batch server needs a slot per concurrent request.
            "--parallel",
            str(backend.max_concurrent),
            # `--ctx-size` is the total and is divided between the slots, so the size
            # one request may reach has to be multiplied back up. Without it the server
            # takes the model's whole training context per slot and fails to allocate.
            "--ctx-size",
            str(
                (context or backend.context_tokens or sizing.MINIMUM_CONTEXT * 4)
                * backend.max_concurrent
            ),
            *backend.args,
        ]

    def start(
        self, name: str, backend: Backend, siblings: dict[str, Backend] | None = None
    ) -> Health:
        """Start one managed backend. Returns why it could not, without raising.

        `siblings` are the other backends, so the context this one is given accounts for
        the memory theirs will hold. Without them each server is sized as though it were
        the only one and the same memory is promised several times over.
        """
        running = self.running.get(name)
        if running is not None:
            if running.process.poll() is None:
                return Health(name=name, url=backend.url, up=True, managed=True)
            # It exited. A record of a dead process would report a server that is gone
            # as running, and every review would fail against nothing.
            self.log.warn(
                "managed server exited",
                reason="server_died",
                backend=name,
                code=running.process.returncode,
                output=self.last_output(name),
            )
            del self.running[name]
        server = self.server_command(backend.engine)
        if server is None:
            from auger.llm import coli

            reason = (
                f"{coli.NAME} is not installed yet. Install it in the Models view."
                if backend.engine == coli.NAME
                else "no model runtime yet. Press Set up in the Models view, and the rig will "
                "fetch one, or point this backend at a server you already run."
            )
            self.log.warn("managed start skipped", reason="no_server", backend=name)
            return Health(name=name, url=backend.url, up=False, reason=reason, managed=True)
        model_path = self.model_path(backend)
        if model_path is None or not model_path.exists():
            reason = f"weights not found at {model_path}. Press Set up in the Models view."
            self.log.warn(
                "managed start skipped", reason="no_weights", backend=name, path=str(model_path)
            )
            return Health(name=name, url=backend.url, up=False, reason=reason, managed=True)
        context = self.context_for(
            name, backend, model_path, self.other_models(name, siblings or {})
        )
        arguments = self.arguments(backend, server, model_path, context)
        try:
            # The server's own output is the only thing that says why it will not
            # compute, why the weights would not load, or which flag it did not
            # understand. Thrown away, a failure reaches the user as a 500 with nothing
            # behind it.
            stream = self.log_file(name).open("wb")
            process = subprocess.Popen(
                arguments,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            stream.close()
        except OSError as error:
            self.log.error("managed start failed", reason="spawn_failed", backend=name, error=error)
            return Health(name=name, url=backend.url, up=False, reason=str(error), managed=True)
        self.running[name] = Managed(name=name, process=process, port=port_of(backend.url))
        self.log.info("managed server started", backend=name, server=server, pid=process.pid)
        return Health(name=name, url=backend.url, up=False, reason="starting", managed=True)

    async def wait_until_up(
        self, client: httpx.AsyncClient, name: str, backend: Backend, timeout: float = START_TIMEOUT
    ) -> Health:
        """Poll until the server answers. A large model takes a minute to load."""
        deadline = asyncio.get_running_loop().time() + timeout
        health = await probe(client, name, backend)
        while not health.up and asyncio.get_running_loop().time() < deadline:
            managed = self.running.get(name)
            if managed and managed.process.poll() is not None:
                return Health(
                    name=name,
                    url=backend.url,
                    up=False,
                    reason=f"the server exited with code {managed.process.returncode}",
                    managed=True,
                )
            await asyncio.sleep(POLL_SECONDS)
            health = await probe(client, name, backend)
        return health

    async def ensure(
        self, client: httpx.AsyncClient, backends: dict[str, Backend]
    ) -> dict[str, Health]:
        """Probe every backend and start the managed ones that do not answer."""
        health = await probe_all(client, backends)
        for name, backend in backends.items():
            if health[name].up or not backend.managed:
                continue
            started = self.start(name, backend, backends)
            health[name] = (
                await self.wait_until_up(client, name, backend)
                if started.reason == "starting"
                else started
            )
        return health

    def stop(self, name: str, backend: Backend | None = None) -> bool:
        """Stop one managed server, and give its memory back.

        A review model holds tens of gigabytes. A user who wants that memory for
        something else must be able to take it back without quitting the rig, and that
        has to work for a server an earlier run left behind as well as one this process
        started.
        """
        managed = self.running.pop(name, None)
        if managed is not None:
            managed.stop()
            self.log.info("managed server stopped", backend=name)
            return True
        if backend is None:
            return False
        pid = self.adopt(backend)
        if pid is None:
            return False
        if not self.end(pid):
            return False
        self.log.info("adopted server stopped", backend=name, pid=pid)
        return True

    def stop_all(self) -> None:
        """Stop every server this rig started, and every one it left behind before.

        Every server is asked to stop first, and only then waited for. One after the
        other would take as long as the sum of them, and the application is quitting.
        """
        managed = list(self.running.values())
        for one in managed:
            one.signal()
        for one in managed:
            one.wait()
            self.log.info("managed server stopped", backend=one.name)
        self.running.clear()
        # The same shape as above: ask them all, then wait. One after the other would
        # take as long as the sum of them, and the application is quitting.
        left = self.adopt_all()
        for pid in left:
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGTERM)
        for pid in left:
            if self.wait_for(pid):
                self.log.info("adopted server stopped", pid=pid)
