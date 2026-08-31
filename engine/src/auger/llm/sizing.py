"""How much context a model can be given on this machine.

Left alone, `llama-server` takes the context the model was trained for. That number is
in the weights and it is large: one of the models the rig offers was trained at 262144
tokens, and the key and value cache for that, across two slots, is 137 GB. The server
allocates it, fails, and then answers every request with a compute error while still
reporting itself healthy. Picking the number by hand instead only moves the guess.

Both halves of the answer are knowable. The model's own file says what it was trained
for and how wide its attention is, in a header that costs a few kilobytes to read. The
machine says how much memory it has. Between them there is one arithmetic step, and no
guess left:

    bytes per token = layers x kv heads x (key width + value width) x element size

Read the file, do the arithmetic, take the largest context that fits.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from auger.log import Logger, create_logger

#: How much of the machine the rig may hold in model weights and caches together. The
#: rest belongs to the user, who is presumably still working while this runs.
MEMORY_SHARE = 0.7

#: `llama-server` keeps the cache at 16 bits unless told otherwise.
ELEMENT_BYTES = 2

#: Below this a review cannot hold a diff and the code around it, so a machine that
#: cannot afford this much is better off saying so than running uselessly.
MINIMUM_CONTEXT = 4096

#: What an embedding request can ever be. It is one chunk of code, never a conversation,
#: and the chunker caps a chunk at 160 lines. Sizing an embedder by what fits would hand
#: it gigabytes it has no use for, and take them from the reviewer, which does.
EMBEDDING_CONTEXT = 8192

#: Contexts are chosen from this ladder rather than an exact fit, because a round number
#: survives a model being swapped for a similar one and is easier to reason about.
LADDER = (4096, 8192, 16384, 32768, 65536, 131072, 262144, 524288, 1048576)

#: GGUF value types that are a fixed number of bytes, as `struct` codes.
_FIXED = {
    0: ("B", 1), 1: ("b", 1), 2: ("H", 2), 3: ("h", 2), 4: ("I", 4),
    5: ("i", 4), 6: ("f", 4), 7: ("?", 1), 10: ("Q", 8), 11: ("q", 8), 12: ("d", 8),
}  # fmt: skip

_WANTED = (
    "general.architecture",
    "block_count",
    "context_length",
    "embedding_length",
    "attention.head_count",
    "attention.head_count_kv",
    "attention.key_length",
    "attention.value_length",
)


@dataclass(frozen=True)
class Model:
    """What a model's own file says about it."""

    architecture: str
    #: The context it was trained for. Asking for more is not better, it is wrong.
    context_length: int
    block_count: int
    head_count: int
    head_count_kv: int
    embedding_length: int
    #: Present on newer models. Older ones divide the embedding by the head count.
    key_length: int = 0
    value_length: int = 0
    weights_bytes: int = 0

    @property
    def head_width(self) -> tuple[int, int]:
        if self.key_length and self.value_length:
            return self.key_length, self.value_length
        width = self.embedding_length // self.head_count if self.head_count else 0
        return width, width

    @property
    def bytes_per_token(self) -> int:
        """What one token of cache costs, across every layer, for one slot."""
        key, value = self.head_width
        return self.block_count * self.head_count_kv * (key + value) * ELEMENT_BYTES

    def cache_bytes(self, context: int, slots: int = 1) -> int:
        """What the key and value cache costs at this size. `--ctx-size` is the total
        across slots, so the slots are already in the context the server is given."""
        return self.bytes_per_token * context * max(1, slots)

    def usable(self) -> bool:
        return self.context_length > 0 and self.bytes_per_token > 0


def _number(handle: BinaryIO, code: str, size: int) -> int:
    value = struct.unpack("<" + code, handle.read(size))[0]
    return int(value)


def _string(handle: BinaryIO) -> str:
    return handle.read(_number(handle, "Q", 8)).decode("utf-8", "replace")


def _skip(handle: BinaryIO, kind: int) -> None:
    if kind == 8:
        _string(handle)
    elif kind == 9:
        inner = _number(handle, "I", 4)
        for _ in range(_number(handle, "Q", 8)):
            _skip(handle, inner)
    else:
        handle.read(_FIXED[kind][1])


def _value(handle: BinaryIO, kind: int) -> str | int | None:
    if kind == 8:
        return _string(handle)
    if kind == 9:
        _skip(handle, 9)
        return None
    code, size = _FIXED[kind]
    if code in ("f", "d"):
        return int(struct.unpack("<" + code, handle.read(size))[0])
    return _number(handle, code, size)


def read(path: Path, log: Logger | None = None) -> Model | None:
    """Read a GGUF header. Returns None when the file is not one, or cannot be read.

    Only the header is touched: a few kilobytes off the front of a file that may be
    tens of gigabytes, with no model loaded and no server started.
    """
    log = (log or create_logger("llm")).bind(component="sizing")
    try:
        with path.open("rb") as handle:
            if handle.read(4) != b"GGUF":
                log.warn("not a gguf file", reason="not_gguf", path=str(path))
                return None
            handle.read(4)  # version
            handle.read(8)  # tensor count
            pairs = _number(handle, "Q", 8)
            found: dict[str, str | int | None] = {}
            for _ in range(pairs):
                key = _string(handle)
                kind = _number(handle, "I", 4)
                if any(key == want or key.endswith("." + want) for want in _WANTED):
                    found[key.split(".", 1)[-1] if key.startswith("general.") else key] = _value(
                        handle, kind
                    )
                else:
                    _skip(handle, kind)
    except (OSError, struct.error, KeyError, IndexError) as error:
        log.warn("could not read the model header", reason="header_failed", error=error)
        return None

    def number(suffix: str) -> int:
        for key, value in found.items():
            if key == suffix or key.endswith("." + suffix):
                return value if isinstance(value, int) else 0
        return 0

    model = Model(
        architecture=str(found.get("architecture", "")),
        context_length=number("context_length"),
        block_count=number("block_count"),
        head_count=number("attention.head_count"),
        head_count_kv=number("attention.head_count_kv"),
        embedding_length=number("embedding_length"),
        key_length=number("attention.key_length"),
        value_length=number("attention.value_length"),
        weights_bytes=path.stat().st_size,
    )
    if not model.usable():
        log.warn(
            "the model header is missing what sizing needs",
            reason="header_incomplete",
            architecture=model.architecture,
        )
        return None
    return model


def machine_bytes() -> int:
    """What this machine has. All of it, before any share is taken."""
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except Exception:
        return 0


def budget(models: list[Model], total: int = 0) -> int:
    """How much memory is left for one model's cache.

    The weights are a fixed cost and every managed server pays it whether or not it is
    answering, so they come off the top. What remains is shared: on a machine that runs
    the reviewer, its adversary and an embedder, sizing each as though it were alone
    would promise the same memory three times.
    """
    total = total or machine_bytes()
    if not total or not models:
        return 0
    weights = sum(model.weights_bytes for model in models)
    return max(0, int(total * MEMORY_SHARE) - weights) // len(models)


def choose(model: Model, slots: int, allowance: int, ceiling: int = 0) -> int:
    """The largest context on the ladder that the model supports and the memory allows.

    Never above what the model was trained for, because the weights beyond that point
    do not exist; never above what fits, because the failure there is the whole server
    rather than one request; and never above `ceiling`, which is what the work asks for.
    A model given more context than its job can use has taken it from one that could.
    """
    if not model.usable():
        return 0
    limit = min(model.context_length, ceiling) if ceiling else model.context_length
    best = 0
    for context in LADDER:
        if context > limit:
            break
        if allowance and model.cache_bytes(context, slots) > allowance:
            break
        best = context
    return best


def clamp(requested: int, model: Model, slots: int, allowance: int) -> tuple[int, str]:
    """Hold a configured context to what is real. Returns the value and why it moved."""
    if not model.usable():
        return requested, ""
    if requested > model.context_length:
        return (
            model.context_length,
            f"{model.architecture} was trained at {model.context_length}, "
            f"so {requested} is not a larger context, only a larger cache",
        )
    if allowance and model.cache_bytes(requested, slots) > allowance:
        fits = choose(model, slots, allowance)
        cost = model.cache_bytes(requested, slots) / 2**30
        return (
            fits or MINIMUM_CONTEXT,
            f"{requested} across {slots} slots needs {cost:.1f} GB of cache, "
            f"which this machine cannot spare",
        )
    return requested, ""
