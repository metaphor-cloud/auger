"""Fetch every model's size, together."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


async def size_of(client: httpx.AsyncClient, url: str) -> int:
    response = await client.head(url)
    return int(response.headers.get("content-length", 0))


async def sizes(client: httpx.AsyncClient, urls: list[str]) -> list[int]:
    results: list[Any] = await asyncio.gather(*(size_of(client, url) for url in urls))
    return [int(one) for one in results]
