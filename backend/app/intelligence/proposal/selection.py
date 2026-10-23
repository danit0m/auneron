from dataclasses import dataclass
from typing import Any

from app.intelligence.planning.authority import SyntheticAuthorityContext
from app.intelligence.planning.capabilities import CapabilityDefinition
from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.planning.capabilities import EffectClass
from app.intelligence.planning.policy import reconcile_capability
from app.intelligence.proposal.contracts import Exclusion
from app.intelligence.proposal.contracts import ExclusionReason


class PolicyUnusable(Exception):
    """The injected policy could not be evaluated (malformed)."""


@dataclass(frozen=True)
class Selection:
    definitions: tuple[CapabilityDefinition, ...]
    exclusions: tuple[Exclusion, ...]

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return tuple(sorted(item.capability_id for item in self.definitions))


def candidates(
    registry: CapabilityRegistry,
) -> tuple[CapabilityDefinition, ...]:
    """
    The ONLY capabilities that may ever be projected. Anything else
    (disabled, not model-proposable, executable) is invisible to every
    later step: it is not even listed as excluded.
    """
    return tuple(
        sorted(
            (
                definition
                for definition in registry.definitions
                if definition.registered
                and definition.enabled
                and definition.llm_proposable
                and not definition.executable
            ),
            key=lambda item: (item.capability_id, item.version),
        )
    )


def _first_exclusion(
    definition: CapabilityDefinition,
    authority: SyntheticAuthorityContext,
    policy: Any,
    all_readable: bool,
    all_manageable: bool,
) -> ExclusionReason | None:
    if not set(definition.required_permissions) <= authority.permissions:
        return ExclusionReason.PERMISSION_MISSING

    if definition.effect_class is EffectClass.READ:
        if not all_readable:
            return ExclusionReason.SCOPE_NOT_READABLE
    elif not all_manageable:
        return ExclusionReason.SCOPE_NOT_MANAGEABLE

    try:
        mismatches = reconcile_capability(definition, policy)
    except Exception:
        raise PolicyUnusable from None

    if mismatches:
        return ExclusionReason.POLICY_MISMATCH

    return None


def select_capabilities(
    registry: CapabilityRegistry,
    authority: SyntheticAuthorityContext,
    policy: Any,
    *,
    all_readable: bool,
    all_manageable: bool,
) -> Selection:
    """
    Narrowing only. The first failing filter decides the reason, in a
    fixed order: permissions, then scope, then policy. A prerequisite
    closure then removes every capability that could never be used
    because its evidence capability is not offered. Nothing here adds,
    enables or grants anything; the full validator re-checks it all.
    """
    kept: list[CapabilityDefinition] = []
    excluded: list[Exclusion] = []

    for definition in candidates(registry):
        reason = _first_exclusion(
            definition, authority, policy, all_readable, all_manageable
        )

        if reason is None:
            kept.append(definition)
        else:
            excluded.append(Exclusion(definition.capability_id, reason))

    changed = True

    while changed:
        changed = False
        offered = {item.capability_id for item in kept}

        for definition in list(kept):
            if any(
                not (set(rule.any_of) & offered)
                for rule in definition.prerequisites
            ):
                kept.remove(definition)
                excluded.append(
                    Exclusion(
                        definition.capability_id,
                        ExclusionReason.PREREQUISITE_UNAVAILABLE,
                    )
                )
                changed = True
                break

    return Selection(
        definitions=tuple(kept),
        exclusions=tuple(sorted(excluded)),
    )
