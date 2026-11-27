"""Run matrix and time estimate per evidence folder (planning only)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Seconds per call. B comes from the 18/11 runs (mean 14.5 s, p95 33 s,
# max 47 s, ~90 tok/s). A has NO measurement for the 2507 instruct model:
# the 3.5 s is the qwen3:4b no-think figure of 16/11 and is a placeholder.
PER_CALL_S = {
    "A": {"nominal": 3.5, "worst": 10.0},
    "B": {"nominal": 14.5, "worst": 50.0},
}
COLD_LOAD_S = {"A": {"nominal": 30.0, "worst": 90.0},
               "B": {"nominal": 30.0, "worst": 90.0}}
OVERHEAD_PER_BLOCK_S = 120.0  # pre-flight, folder creation, hashing, notes


@dataclass(frozen=True)
class Block:
    block_id: str
    arm: str
    dataset: str
    n_cases: int
    seed: int
    order_seed: int | None
    rep: str
    required: bool
    purpose: str
    call_scale: float = 1.0  # B+ calls may be up to 2x longer (8192 tokens)


BLOCKS = (
    Block("A-v1", "A", "v1", 80, 0, None, "main", True, "desenvolvimento"),
    Block("A-v2", "A", "v2", 80, 0, None, "main", True, "desenvolvimento"),
    Block("A-v3", "A", "v3", 168, 0, None, "main", True, "decisao"),
    Block("C-v3", "A", "v3", 168, 0, 2, "order2", True, "ordem aleatoria 2"),
    Block("SA1-v3", "A", "v3", 40, 1, None, "seed1", True, "estabilidade"),
    Block("SA2-v3", "A", "v3", 40, 2, None, "seed2", True, "estabilidade"),
    Block("B-v3", "B", "v3", 168, 0, None, "main", True, "decisao"),
    Block("SB1-v3", "B", "v3", 40, 1, None, "seed1", True, "estabilidade"),
    Block("SB2-v3", "B", "v3", 40, 2, None, "seed2", True, "estabilidade"),
    Block("EB0-v3", "B", "v3", 40, 0, None, "repeat0", True, "determinismo"),
    Block("B-v1", "B", "v1", 80, 0, None, "main", False,
          "reexecucao opcional (ha dados de 18/11)"),
    Block("B-v2", "B", "v2", 80, 0, None, "main", False,
          "reexecucao opcional (ha dados de 18/11)"),
    Block("RB-trunc", "B", "v3", 10, 0, None, "budget8192", False,
          "so casos truncados em B-v3; num_predict 8192, num_ctx 12288",
          2.0),
)


def estimate_minutes(block: Block) -> dict[str, float]:
    per = PER_CALL_S[block.arm]
    cold = COLD_LOAD_S[block.arm]
    out = {}

    for key in ("nominal", "worst"):
        seconds = (
            block.n_cases * per[key] * block.call_scale
            + cold[key]
            + OVERHEAD_PER_BLOCK_S
        )
        out[key] = round(seconds / 60, 1)

    return out


def totals(include_optional: bool) -> dict[str, float]:
    nominal = worst = 0.0

    for block in BLOCKS:
        if block.required or include_optional:
            est = estimate_minutes(block)
            nominal += est["nominal"]
            worst += est["worst"]

    return {"nominal_min": round(nominal, 1), "worst_min": round(worst, 1)}


def as_table() -> list[dict[str, Any]]:
    rows = []

    for block in BLOCKS:
        est = estimate_minutes(block)
        rows.append(
            {
                "block": block.block_id, "arm": block.arm,
                "dataset": block.dataset, "cases": block.n_cases,
                "seed": block.seed, "order_seed": block.order_seed,
                "required": block.required,
                "nominal_min": est["nominal"], "worst_min": est["worst"],
                "purpose": block.purpose,
            }
        )

    return rows
