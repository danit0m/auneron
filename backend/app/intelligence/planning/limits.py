from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class PlanLimits:
    """
    Deterministic plan limits (INT-IMPL-1). LAB VALUES: they are code
    constants, part of the registry digest, and every change needs PO
    approval (docs/intelligence/PLANNING_CONTRACT.md, "Limits").
    """

    max_steps: int = 8
    max_depth: int = 4
    max_fan_out: int = 3
    max_dependencies: int = 3
    max_mutating_steps: int = 1
    max_json_bytes: int = 32 * 1024
    max_json_depth: int = 10
    max_summary_chars: int = 280
    max_rationale_chars: int = 400
    max_preconditions: int = 4
    max_expected_outcomes: int = 4
    max_integer: int = 2**53 - 1

    def descriptor(self) -> dict[str, int]:
        return {
            "max_dependencies": self.max_dependencies,
            "max_depth": self.max_depth,
            "max_expected_outcomes": self.max_expected_outcomes,
            "max_fan_out": self.max_fan_out,
            "max_integer": self.max_integer,
            "max_json_bytes": self.max_json_bytes,
            "max_json_depth": self.max_json_depth,
            "max_mutating_steps": self.max_mutating_steps,
            "max_preconditions": self.max_preconditions,
            "max_rationale_chars": self.max_rationale_chars,
            "max_steps": self.max_steps,
            "max_summary_chars": self.max_summary_chars,
        }


PLAN_LIMITS: Final[PlanLimits] = PlanLimits()
