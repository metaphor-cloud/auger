"""Find or start the model servers.

The rig prefers a server that the user already runs, because that server holds the model
the user chose and it may already be warm. It starts one of its own only when nothing
answers, and it says plainly why when it cannot.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from auger.config.schema import Backend
from auger.log import Logger, create_logger
from auger.sandbox.which import find

PROBE_TIMEOUT = 2.0
START_TIMEOUT = 180.0
POLL_SECONDS = 1.0
#: Where an OpenAI-compatible server usually listens. `llama-server` and
#: `mlx-openai-server` both default into this range.
#: How long a server gets to stop politely before it is ended.
ADOPT_TIMEOUT = 8.0

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

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


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

    def __init__(self, models_dir: Path, log: Logger | None = None) -> None:
        self.models_dir = models_dir
        self.log = (log or create_logger("llm")).bind(component="supervisor")
        self.running: dict[str, Managed] = {}

    def server_command(self) -> str | None:
        """The server to run: one the user has, or the one the rig installed itself.

        A graphical application has a narrow PATH, and a first run has nothing installed
        at all, so neither `PATH` alone nor `which` alone is enough.
        """
        from auger.llm import runtime

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

    def end(self, pid: int) -> bool:
        """Ask a server to stop, and insist if it does not.

        A server that ignores the polite signal still holds the memory, so waiting
        politely for ever is the same as never stopping it at all.
        """
        import signal

        import psutil

        try:
            process = psutil.Process(pid)
            process.terminate()
            process.wait(timeout=ADOPT_TIMEOUT)
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

    def arguments(self, backend: Backend, server: str, model_path: Path) -> list[str]:
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
            *backend.args,
        ]

    def start(self, name: str, backend: Backend) -> Health:
        """Start one managed backend. Returns why it could not, without raising."""
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
            )
            del self.running[name]
        server = self.server_command()
        if server is None:
            reason = (
                "no model runtime yet. Press Set up in the Models view, and the rig will "
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
        arguments = self.arguments(backend, server, model_path)
        try:
            process = subprocess.Popen(
                arguments,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
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
            started = self.start(name, backend)
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
        """Stop every server this rig started, and every one it left behind before."""
        for managed in list(self.running.values()):
            managed.stop()
            self.log.info("managed server stopped", backend=managed.name)
        self.running.clear()
        for pid in self.adopt_all():
            if self.end(pid):
                self.log.info("adopted server stopped", pid=pid)
