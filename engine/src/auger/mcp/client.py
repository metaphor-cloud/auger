"""Talk to one MCP server.

A server is a subprocess or a remote endpoint that the user attached. It runs outside the
sandbox, so the rig gives it as little as it can: a named set of environment variables
and nothing else. The engine token and every forge token stay here.

A session is opened per call rather than held open. A review runs every few minutes at
most, and a held session is a process that outlives its purpose.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auger.config.schema import McpServer
from auger.log import Logger, create_logger
from auger.net.allowlist import Allowlist

CONNECT_TIMEOUT = 30.0


class McpError(RuntimeError):
    """The server could not be reached, or it refused."""


@dataclass(frozen=True)
class Access:
    """What an http server may be reached through, and where its token is kept.

    An http server is a destination like any other, so it obeys the same allowlist as
    the rest of the engine. Before this existed, the SDK built its own client and no
    rule applied to it.
    """

    allowlist: Allowlist = field(default_factory=Allowlist)
    home: Path = field(default_factory=lambda: Path("~/.auger").expanduser())


@dataclass(frozen=True)
class Tool:
    server: str
    name: str
    description: str
    schema: dict[str, Any]

    @property
    def qualified(self) -> str:
        return f"{self.server}.{self.name}"


@dataclass(frozen=True)
class ToolResult:
    tool: str
    text: str
    is_error: bool = False


def server_environment(config: McpServer) -> dict[str, str]:
    """What the server process may see.

    Only the variables the user named, plus a minimal PATH. A secret the rig holds is
    never passed on, because the rig cannot know what the server does with it.
    """
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", ""),
    }
    for name in config.pass_env:
        value = os.environ.get(name)
        if value:
            environment[name] = value
    environment.update(config.env)
    return environment


@asynccontextmanager
async def session(
    config: McpServer,
    log: Logger,
    name: str = "",
    access: Access | None = None,
) -> AsyncIterator[Any]:
    """Open a session to one server, and close it however the block ends."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    from auger.net.client import guarded_mcp_client

    access = access or Access()
    http: Any = None
    if config.transport == "stdio":
        if not config.command:
            raise McpError("a stdio server needs a command")
        parameters = StdioServerParameters(
            command=config.command, args=list(config.args), env=server_environment(config)
        )
        transport = stdio_client(parameters)
    else:
        if not config.url:
            raise McpError("an http server needs a url")
        auth = None
        if config.auth == "oauth":
            from auger.mcp.oauth import background_provider

            auth = background_provider(name or config.url, config, access.home)
        http = guarded_mcp_client(access.allowlist, log, auth=auth)
        transport = streamable_http_client(config.url, http_client=http)

    try:
        async with transport as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as client:
                await asyncio.wait_for(client.initialize(), CONNECT_TIMEOUT)
                yield client
    except (McpError, asyncio.CancelledError):
        raise
    except Exception as error:
        log.warn("mcp session failed", reason="mcp_unreachable", error=error)
        raise McpError(str(error)) from error
    finally:
        if http is not None:
            await http.aclose()


async def list_tools(
    name: str,
    config: McpServer,
    log: Logger | None = None,
    access: Access | None = None,
) -> list[Tool]:
    log = (log or create_logger("mcp")).bind(component="mcp", server=name)
    async with session(config, log, name, access) as client:
        listing = await asyncio.wait_for(client.list_tools(), CONNECT_TIMEOUT)
    return [
        Tool(
            server=name,
            name=tool.name,
            description=(tool.description or "").strip(),
            schema=dict(tool.input_schema or {}),
        )
        for tool in listing.tools
    ]


async def call_tool(
    name: str,
    config: McpServer,
    tool: str,
    arguments: dict[str, Any],
    log: Logger | None = None,
    access: Access | None = None,
) -> ToolResult:
    """Call one tool. The result is text, and it is data, never an instruction."""
    log = (log or create_logger("mcp")).bind(component="mcp", server=name, tool=tool)
    async with session(config, log, name, access) as client:
        try:
            result = await asyncio.wait_for(
                client.call_tool(tool, arguments), config.timeout_seconds
            )
        except TimeoutError as error:
            log.warn("tool call timed out", reason="tool_timeout", seconds=config.timeout_seconds)
            raise McpError(f"{tool} took longer than {config.timeout_seconds}s") from error
    text = "\n".join(
        str(getattr(block, "text", "")) for block in result.content if getattr(block, "text", "")
    )
    log.info("tool called", is_error=bool(result.is_error), bytes=len(text))
    return ToolResult(tool=f"{name}.{tool}", text=text, is_error=bool(result.is_error))
