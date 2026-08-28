"""The egress proxy.

Every request that leaves this machine passes through here, and every one of them is
logged with its destination and its verdict. That log is the evidence that the user's
code stayed on the machine.

The engine's own HTTP client checks the same allowlist directly. The proxy covers what
the client cannot: a subprocess such as a forge command line tool, or an MCP server,
which the rig starts with `HTTPS_PROXY` pointed here.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from auger.log import Logger, create_logger
from auger.net.allowlist import Allowlist, Destination

CONNECT_TIMEOUT = 15.0
#: The length is counted, never written by hand. A hand written one drifts the moment
#: the words change, and the client then waits for bytes that never arrive.
_REFUSED_BODY = b"auger refused this destination. It is not on the allowlist.\n"
REFUSED = (
    b"HTTP/1.1 403 Forbidden\r\n"
    b"Content-Type: text/plain\r\n"
    b"Content-Length: " + str(len(_REFUSED_BODY)).encode() + b"\r\n"
    b"Connection: close\r\n\r\n" + _REFUSED_BODY
)
BAD_REQUEST = b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
UNREACHABLE = b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"
ESTABLISHED = b"HTTP/1.1 200 Connection Established\r\n\r\n"


@dataclass
class ProxyStats:
    allowed: int = 0
    refused: int = 0
    failed: int = 0
    refused_hosts: list[str] = field(default_factory=list)

    def record_refusal(self, destination: str) -> None:
        self.refused += 1
        if destination not in self.refused_hosts:
            self.refused_hosts.append(destination)
        del self.refused_hosts[:-20]


class EgressProxy:
    """An HTTP proxy that only reaches the allowlist."""

    def __init__(self, allowlist: Allowlist, log: Logger | None = None) -> None:
        self.allowlist = allowlist
        self.log = (log or create_logger("egress")).bind(component="egress")
        self.stats = ProxyStats()
        self._server: asyncio.AbstractServer | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def proxy_env(self) -> dict[str, str]:
        """Environment for a subprocess, so its traffic meets the allowlist too."""
        return {
            "HTTP_PROXY": self.url,
            "HTTPS_PROXY": self.url,
            "http_proxy": self.url,
            "https_proxy": self.url,
            "ALL_PROXY": self.url,
            "NO_PROXY": "",
            "no_proxy": "",
        }

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self._server = await asyncio.start_server(self._handle, host, port)
        self.port = self._server.sockets[0].getsockname()[1]
        self.log.info("egress proxy listening", port=self.port, allowed=str(self.allowlist))
        return self.port

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._serve(reader, writer)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except Exception as error:
            self.log.error("proxy connection failed", reason="proxy_error", error=error)
        finally:
            writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), CONNECT_TIMEOUT)
        except asyncio.IncompleteReadError:
            return  # The client hung up before it sent a request.
        except (asyncio.LimitOverrunError, ValueError, TimeoutError):
            writer.write(BAD_REQUEST)
            await writer.drain()
            return
        request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = request_line.split()
        if len(parts) < 3:
            writer.write(BAD_REQUEST)
            await writer.drain()
            return
        method, target = parts[0].upper(), parts[1]
        destination = self._destination(method, target)
        if destination is None:
            writer.write(BAD_REQUEST)
            await writer.drain()
            return
        if not self.allowlist.allows(destination.host, destination.port):
            self.stats.record_refusal(str(destination))
            self.log.warn(
                "egress refused",
                reason="not_allowlisted",
                destination=str(destination),
                method=method,
            )
            writer.write(REFUSED)
            await writer.drain()
            return
        await self._forward(method, head, destination, reader, writer)

    @staticmethod
    def _destination(method: str, target: str) -> Destination | None:
        if method == "CONNECT":
            return Destination.parse(target)
        if "://" in target:
            return Destination.parse(target)
        return None

    async def _forward(
        self,
        method: str,
        head: bytes,
        destination: Destination,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        started = time.monotonic()
        try:
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(destination.host, destination.port),
                timeout=CONNECT_TIMEOUT,
            )
        except (OSError, TimeoutError) as error:
            self.stats.failed += 1
            self.log.warn(
                "egress unreachable",
                reason="connect_failed",
                destination=str(destination),
                error=error,
            )
            client_writer.write(UNREACHABLE)
            await client_writer.drain()
            return

        self.stats.allowed += 1
        if method == "CONNECT":
            client_writer.write(ESTABLISHED)
            await client_writer.drain()
        else:
            # A plain request keeps its head. The origin server ignores the absolute URI.
            upstream_writer.write(head)
            await upstream_writer.drain()

        sent, received = await self._pipe(
            client_reader, client_writer, upstream_reader, upstream_writer
        )
        self.log.info(
            "egress allowed",
            destination=str(destination),
            method=method,
            sent_bytes=sent,
            received_bytes=received,
            seconds=round(time.monotonic() - started, 3),
        )

    @staticmethod
    async def _pipe(
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> tuple[int, int]:
        counts = [0, 0]

        async def copy(
            source: asyncio.StreamReader, sink: asyncio.StreamWriter, index: int
        ) -> None:
            try:
                while chunk := await source.read(65536):
                    counts[index] += len(chunk)
                    sink.write(chunk)
                    await sink.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
            finally:
                if not sink.is_closing():
                    sink.close()

        await asyncio.gather(
            copy(client_reader, upstream_writer, 0),
            copy(upstream_reader, client_writer, 1),
            return_exceptions=True,
        )
        return counts[0], counts[1]
