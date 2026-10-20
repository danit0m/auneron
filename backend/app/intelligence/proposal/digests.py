from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import Final

from app.intelligence.planning.canonical import digest
from app.intelligence.proposal.contracts import Exclusion


# Domain separation, in the same style as the planning digests
# (auneron.plan.v1.*, auneron.capreg.v1): a digest computed for one
# purpose can never be replayed as another. registry_digest and
# context_digest are NOT redefined here: they come from the planning
# package unchanged.
DOMAIN_POLICY: Final[bytes] = b"auneron.proj.v1.policy\n"
DOMAIN_SCHEMA: Final[bytes] = b"auneron.proj.v1.schema\n"
DOMAIN_INSTRUCTIONS: Final[bytes] = b"auneron.proj.v1.instructions\n"
DOMAIN_PROJECTION: Final[bytes] = b"auneron.proj.v1.projection\n"

_ACTION_FIELDS: Final[tuple[str, ...]] = (
    "capability_kind",
    "initiation_actor",
    "authority_model",
    "required_authority",
    "execution_corridor",
)


def _text(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("Expected text.")

    return value


def _text_set(value: Any) -> list[str]:
    if not isinstance(value, (frozenset, set)):
        raise ValueError("Expected a set of text.")

    return sorted(_text(item) for item in value)


def _mapping(value: Any) -> Mapping[Any, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("Expected a mapping.")

    return value


def policy_material(policy: Any) -> dict[str, Any]:
    """
    Canonical description of an injected PolicyReference. Strict: any
    unexpected type raises ValueError, which the builder turns into a
    typed rejection. A missing approver is an absent key, never null.
    """
    actions: dict[str, Any] = {}

    for key, entry in _mapping(policy.actions).items():
        actions[_text(key)] = {
            name: _text(getattr(entry, name)) for name in _ACTION_FIELDS
        }

    autonomy: dict[str, Any] = {}

    for effect, entry in _mapping(policy.autonomy_by_effect).items():
        item = {
            "disposition": _text(entry.disposition),
            "risk_level": _text(entry.risk_level),
        }

        if entry.approver_permission is not None:
            item["approver_permission"] = _text(entry.approver_permission)

        autonomy[_text(effect)] = item

    routes: dict[str, Any] = {}

    for capability_id, permissions in _mapping(
        policy.read_route_permissions
    ).items():
        routes[_text(capability_id)] = _text_set(permissions)

    return {
        "actions": actions,
        "autonomy": autonomy,
        "known_permissions": _text_set(policy.known_permissions),
        "read_route_permissions": routes,
        "source": _text(policy.source),
    }


def policy_digest(policy: Any) -> str:
    return digest(DOMAIN_POLICY, policy_material(policy))


def schema_digest(schema: Mapping[str, Any]) -> str:
    return digest(DOMAIN_SCHEMA, schema)


def instructions_digest(template_id: str, text: str) -> str:
    return digest(
        DOMAIN_INSTRUCTIONS,
        {"template_id": template_id, "text": text},
    )


def projection_material(
    *,
    contract: str,
    template_id: str,
    registry_digest: str,
    context_digest: str,
    policy_digest: str,
    profile_name: str,
    profile_max_output_tokens_cap: int,
    max_schema_bytes: int,
    max_account_refs: int,
    plan_limits: Sequence[tuple[str, int]],
    objective_ref: str,
    plan_version: int,
    account_refs: Sequence[str],
    capability_ids: Sequence[str],
    exclusions: Sequence[Exclusion],
    schema_digest: str,
    instructions_digest: str,
) -> dict[str, Any]:
    """Everything the projection depends on, in canonical order."""
    return {
        "contract": contract,
        "template_id": template_id,
        "registry_digest": registry_digest,
        "context_digest": context_digest,
        "policy_digest": policy_digest,
        "profile": {
            "name": profile_name,
            "max_output_tokens_cap": profile_max_output_tokens_cap,
        },
        "limits": {
            "projection": {
                "max_schema_bytes": max_schema_bytes,
                "max_account_refs": max_account_refs,
            },
            "plan": {name: value for name, value in plan_limits},
        },
        "objective_ref": objective_ref,
        "plan_version": plan_version,
        "account_refs": sorted(account_refs),
        "capability_ids": sorted(capability_ids),
        "exclusions": sorted(
            [item.capability_id, item.reason.value] for item in exclusions
        ),
        "schema_digest": schema_digest,
        "instructions_digest": instructions_digest,
    }


def projection_digest(material: Mapping[str, Any]) -> str:
    return digest(DOMAIN_PROJECTION, material)
