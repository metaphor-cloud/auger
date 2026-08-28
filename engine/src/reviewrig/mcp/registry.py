"""The attached MCP servers, and what each one offers.

A tool list is read once and cached. Listing costs a process start, and a review that
paid that price on every run would be slower than the review itself.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from reviewrig.config.schema import Config, McpServer
from reviewrig.log import Logger, create_logger
from reviewrig.mcp.allowlist import ToolAllowlist
from reviewrig.mcp.client import McpError, Tool, ToolResult, call_tool, list_tools

#: How much of a tool's answer reaches the prompt. A tool that returns a whole file
#: would otherwise push the diff out of the model's context.
MAX_RESULT_CHARS = 8000


@dataclass
class ServerState:
    name: str
    config: McpServer
    tools: list[Tool] = field(default_factory=list)
    reachable: bool = False
    reason: str | None = None


class McpRegistry:
    def __init__(self, config: Config, log: Logger | None = None) -> None:
        self.log = (log or create_logger("mcp")).bind(component="mcp")
        self.servers: dict[str, ServerState] = {}
        self.reload(config)

    def reload(self, config: Config) -> None:
        kept = {
            name: self.servers.get(name) or ServerState(name=name, config=settings)
            for name, settings in config.mcp.items()
            if settings.enabled
        }
        for name, state in kept.items():
            state.config = config.mcp[name]
        self.servers = kept

    async def refresh(self, names: set[str] | None = None) -> None:
        """Read the tool list from each server. Never raises."""
        wanted = [state for name, state in self.servers.items() if names is None or name in names]
        await asyncio.gather(*(self._refresh_one(state) for state in wanted))

    async def _refresh_one(self, state: ServerState) -> None:
        try:
            state.tools = await list_tools(state.name, state.config, self.log)
            state.reachable = True
            state.reason = None
            self.log.info("mcp server ready", server=state.name, tools=len(state.tools))
        except (McpError, Exception) as error:
            state.tools = []
            state.reachable = False
            state.reason = str(error)
            self.log.warn(
                "mcp server unavailable", reason="mcp_unreachable", server=state.name, error=error
            )

    def tools_for(self, allowlist: ToolAllowlist) -> list[Tool]:
        """Every known tool that this policy allows."""
        if allowlist.empty:
            return []
        return [
            tool
            for state in self.servers.values()
            for tool in state.tools
            if allowlist.allows(state.name, tool.name)
        ]

    async def call(
        self, allowlist: ToolAllowlist, qualified: str, arguments: dict[str, Any]
    ) -> ToolResult:
        """Call a tool, but only when the policy named it."""
        server, _, tool = qualified.partition(".")
        if not tool or not allowlist.allows(server, tool):
            self.log.warn(
                "tool call refused",
                reason="not_allowlisted",
                tool=qualified,
                allowed=str(allowlist),
            )
            raise McpError(f"{qualified} is not on this repository's tool list")
        state = self.servers.get(server)
        if state is None:
            raise McpError(f"no MCP server named {server!r}")
        result = await call_tool(server, state.config, tool, arguments, self.log)
        if len(result.text) > MAX_RESULT_CHARS:
            return ToolResult(
                tool=result.tool,
                text=result.text[:MAX_RESULT_CHARS] + "\n[result truncated]",
                is_error=result.is_error,
            )
        return result
