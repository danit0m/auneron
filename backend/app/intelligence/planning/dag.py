from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.intelligence.planning.codes import RejectionCode
from app.intelligence.planning.codes import Violation
from app.intelligence.planning.codes import make_violation
from app.intelligence.planning.limits import PLAN_LIMITS
from app.intelligence.planning.limits import PlanLimits


@dataclass(frozen=True)
class DagAnalysis:
    violations: tuple[Violation, ...]
    order: tuple[str, ...]
    depth: int
    max_fan_out: int
    ancestors: Mapping[str, frozenset[str]]


def analyze_dag(
    steps: Sequence[Mapping[str, Any]],
    limits: PlanLimits = PLAN_LIMITS,
) -> DagAnalysis:
    """
    Deterministic, LLM-independent analysis of the step graph. Expects
    structurally valid steps (stage 3): `step_key` and `depends_on`.

    depth counts NODES on the longest dependency chain (a lone step has
    depth 1); fan-out is the number of steps that depend on one step.
    Duplicate step keys make the graph ambiguous, so they are reported
    alone.
    """
    violations: list[Violation] = []
    index_of: dict[str, int] = {}

    for index, step in enumerate(steps):
        key = step["step_key"]

        if key in index_of:
            violations.append(
                make_violation(
                    RejectionCode.DAG_DUPLICATE_STEP_KEY,
                    f"$.steps[{index}].step_key",
                )
            )
        else:
            index_of[key] = index

    if violations:
        return DagAnalysis(tuple(sorted(violations)), (), 0, 0, {})

    edges: dict[str, list[str]] = {key: [] for key in index_of}

    for index, step in enumerate(steps):
        key = step["step_key"]
        seen: set[str] = set()

        for position, target in enumerate(step["depends_on"]):
            path = f"$.steps[{index}].depends_on[{position}]"

            if target == key:
                violations.append(
                    make_violation(RejectionCode.DAG_SELF_DEPENDENCY, path)
                )
            elif target in seen:
                violations.append(
                    make_violation(
                        RejectionCode.DAG_DUPLICATE_DEPENDENCY, path
                    )
                )
            elif target not in index_of:
                violations.append(
                    make_violation(RejectionCode.DAG_UNKNOWN_DEPENDENCY, path)
                )
            else:
                edges[key].append(target)

            seen.add(target)

    dependents: dict[str, int] = {key: 0 for key in index_of}

    for key, targets in edges.items():
        for target in targets:
            dependents[target] += 1

    max_fan_out = max(dependents.values(), default=0)

    for key in sorted(dependents):
        if dependents[key] > limits.max_fan_out:
            violations.append(
                make_violation(
                    RejectionCode.DAG_FAN_OUT_EXCEEDED,
                    f"$.steps[{index_of[key]}]",
                )
            )

    # Kahn, with a sorted frontier so the order is reproducible.
    remaining = {key: len(targets) for key, targets in edges.items()}
    children: dict[str, list[str]] = {key: [] for key in index_of}

    for key, targets in edges.items():
        for target in targets:
            children[target].append(key)

    frontier = sorted(key for key, count in remaining.items() if count == 0)
    order: list[str] = []
    depth_of: dict[str, int] = {}
    ancestors: dict[str, frozenset[str]] = {}

    while frontier:
        key = frontier.pop(0)
        order.append(key)
        depth_of[key] = 1 + max(
            (depth_of[target] for target in edges[key]), default=0
        )
        collected: set[str] = set()

        for target in edges[key]:
            collected.add(target)
            collected |= ancestors[target]

        ancestors[key] = frozenset(collected)

        for child in sorted(children[key]):
            remaining[child] -= 1

            if remaining[child] == 0:
                frontier.append(child)
                frontier.sort()

    depth = 0

    if len(order) != len(index_of):
        violations.append(make_violation(RejectionCode.DAG_CYCLE, "$.steps"))
        ancestors = {}
    else:
        depth = max(depth_of.values(), default=0)

        if depth > limits.max_depth:
            violations.append(
                make_violation(RejectionCode.DAG_DEPTH_EXCEEDED, "$.steps")
            )

    return DagAnalysis(
        violations=tuple(sorted(violations)),
        order=tuple(order),
        depth=depth,
        max_fan_out=max_fan_out,
        ancestors=ancestors,
    )
