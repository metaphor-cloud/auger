"""Working out how much context a model can be given.

Every number here is checked against arithmetic rather than against a recorded output,
because the point of the module is that the answer is calculable and not a guess.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from auger.llm import sizing
from auger.llm.sizing import Model

# The three shapes that matter, taken from real headers: a reviewer with narrow
# attention, an adversary with very wide attention, and an embedder with neither.
NARROW = Model(
    architecture="muse-glimmer",
    context_length=131072,
    block_count=52,
    head_count=32,
    head_count_kv=2,
    embedding_length=6656,
    key_length=128,
    value_length=128,
    weights_bytes=17 * 2**30,
)
WIDE = Model(
    architecture="qwen35",
    context_length=262144,
    block_count=64,
    head_count=24,
    head_count_kv=4,
    embedding_length=5120,
    key_length=256,
    value_length=256,
    weights_bytes=18 * 2**30,
)
OLD = Model(
    architecture="qwen2",
    context_length=32768,
    block_count=28,
    head_count=28,
    head_count_kv=4,
    embedding_length=3584,
    weights_bytes=4 * 2**30,
)


# --- the arithmetic --------------------------------------------------------------------


def test_a_token_costs_every_layer_of_keys_and_values() -> None:
    assert NARROW.bytes_per_token == 52 * 2 * (128 + 128) * 2


def test_a_model_without_head_widths_divides_the_embedding() -> None:
    """Older headers do not declare the head width, so it comes from the embedding."""
    assert OLD.head_width == (3584 // 28, 3584 // 28)


def test_slots_multiply_the_cache() -> None:
    assert WIDE.cache_bytes(1000, 4) == WIDE.cache_bytes(1000, 1) * 4


def test_wide_attention_is_what_makes_a_context_expensive() -> None:
    """The same context costs five times as much on the adversary as on the reviewer,
    which is why one of them can hold its whole training context and the other cannot."""
    assert WIDE.bytes_per_token > NARROW.bytes_per_token * 4


# --- choosing --------------------------------------------------------------------------


def test_a_model_gets_its_whole_context_when_it_fits() -> None:
    assert sizing.choose(NARROW, slots=2, allowance=20 * 2**30) == 131072


def test_a_context_the_machine_cannot_hold_is_not_chosen() -> None:
    """262144 tokens across two slots is 128 GB of cache. This is the case that took
    every server on the machine down."""
    chosen = sizing.choose(WIDE, slots=2, allowance=17 * 2**30)
    assert chosen < WIDE.context_length
    assert WIDE.cache_bytes(chosen, 2) <= 17 * 2**30


def test_nothing_above_what_the_model_was_trained_for() -> None:
    """The weights past that point do not exist, so a larger number buys only cache."""
    assert sizing.choose(OLD, slots=1, allowance=10**15) == 32768


def test_a_ceiling_holds_a_model_to_what_its_work_needs() -> None:
    assert sizing.choose(NARROW, slots=1, allowance=10**15, ceiling=8192) == 8192


def test_a_machine_with_nothing_spare_chooses_nothing() -> None:
    assert sizing.choose(WIDE, slots=2, allowance=1) == 0


def test_every_choice_is_on_the_ladder() -> None:
    for allowance in (2**30, 8 * 2**30, 64 * 2**30, 10**15):
        chosen = sizing.choose(NARROW, slots=2, allowance=allowance)
        assert chosen == 0 or chosen in sizing.LADDER


# --- the budget ------------------------------------------------------------------------


def test_the_weights_come_off_before_the_caches_are_shared() -> None:
    total = 128 * 2**30
    allowance = sizing.budget([NARROW, WIDE, OLD], total)
    weights = NARROW.weights_bytes + WIDE.weights_bytes + OLD.weights_bytes
    assert allowance == (int(total * sizing.MEMORY_SHARE) - weights) // 3


def test_a_machine_that_cannot_hold_its_weights_has_nothing_to_share() -> None:
    assert sizing.budget([NARROW, WIDE], total=8 * 2**30) == 0


def test_the_share_leaves_the_machine_to_its_owner() -> None:
    assert sizing.budget([OLD], total=100 * 2**30) < 100 * 2**30


# --- clamping a configured value -------------------------------------------------------


def test_a_number_above_the_model_is_reduced_and_explained() -> None:
    value, why = sizing.clamp(999_999, OLD, slots=1, allowance=10**15)
    assert value == 32768
    assert "trained" in why


def test_a_number_the_machine_cannot_hold_is_reduced_and_explained() -> None:
    value, why = sizing.clamp(262144, WIDE, slots=2, allowance=8 * 2**30)
    assert value < 262144
    assert "GB" in why


def test_a_number_that_is_fine_is_left_alone() -> None:
    value, why = sizing.clamp(16384, NARROW, slots=2, allowance=64 * 2**30)
    assert (value, why) == (16384, "")


# --- reading a real file ---------------------------------------------------------------


def write_gguf(path: Path, architecture: str, values: dict[str, int]) -> Path:
    """The smallest file the reader will accept, built to the format's own layout."""
    out = bytearray(b"GGUF")
    out += struct.pack("<I", 3)  # version
    out += struct.pack("<Q", 0)  # tensor count
    out += struct.pack("<Q", len(values) + 1)

    def string(text: str) -> bytes:
        raw = text.encode()
        return struct.pack("<Q", len(raw)) + raw

    out += string("general.architecture") + struct.pack("<I", 8) + string(architecture)
    for key, value in values.items():
        out += string(f"{architecture}.{key}") + struct.pack("<I", 4) + struct.pack("<I", value)
    path.write_bytes(bytes(out))
    return path


FIELDS = {
    "context_length": 65536,
    "block_count": 40,
    "embedding_length": 5120,
    "attention.head_count": 40,
    "attention.head_count_kv": 8,
    "attention.key_length": 128,
    "attention.value_length": 128,
}


def test_a_header_is_read_without_loading_the_model(tmp_path: Path) -> None:
    path = write_gguf(tmp_path / "m.gguf", "testarch", FIELDS)
    model = sizing.read(path)
    assert model is not None
    assert model.architecture == "testarch"
    assert model.context_length == 65536
    assert model.bytes_per_token == 40 * 8 * (128 + 128) * 2


def test_the_weights_size_comes_from_the_file(tmp_path: Path) -> None:
    path = write_gguf(tmp_path / "m.gguf", "testarch", FIELDS)
    model = sizing.read(path)
    assert model is not None and model.weights_bytes == path.stat().st_size


def test_something_that_is_not_a_model_reads_as_nothing(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("this is not a model")
    assert sizing.read(path) is None


def test_a_header_missing_what_sizing_needs_reads_as_nothing(tmp_path: Path) -> None:
    """Better to say so than to size from zeros and divide by one of them."""
    path = write_gguf(tmp_path / "m.gguf", "testarch", {"context_length": 4096})
    assert sizing.read(path) is None


def test_a_missing_file_reads_as_nothing(tmp_path: Path) -> None:
    assert sizing.read(tmp_path / "gone.gguf") is None


def test_a_truncated_file_reads_as_nothing(tmp_path: Path) -> None:
    path = write_gguf(tmp_path / "m.gguf", "testarch", FIELDS)
    path.write_bytes(path.read_bytes()[:20])
    assert sizing.read(path) is None


@pytest.mark.parametrize("slots", [1, 2, 8])
def test_what_is_chosen_always_fits(tmp_path: Path, slots: int) -> None:
    """The property the whole module exists for."""
    path = write_gguf(tmp_path / "m.gguf", "testarch", FIELDS)
    model = sizing.read(path)
    assert model is not None
    allowance = 4 * 2**30
    chosen = sizing.choose(model, slots, allowance)
    assert model.cache_bytes(chosen, slots) <= allowance
