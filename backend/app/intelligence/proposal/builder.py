import copy
import dataclasses
import json
from typing import Any

from app.intelligence.planning.authority import AccountAccess
from app.intelligence.planning.authority import SyntheticAuthorityContext
from app.intelligence.planning.canonical import canonical_bytes
from app.intelligence.planning.capabilities import CapabilityDefinition
from app.intelligence.planning.capabilities import CapabilityRegistry
from app.intelligence.planning.limits import PlanLimits
from app.intelligence.planning.policy import PolicyReference
from app.intelligence.proposal.contracts import ACCOUNT_REF_PATTERN
from app.intelligence.proposal.contracts import DIGEST_PATTERN
from app.intelligence.proposal.contracts import INSTRUCTIONS_TEMPLATE_ID
from app.intelligence.proposal.contracts import INSTRUCTIONS_TEXT
from app.intelligence.proposal.contracts import MAX_ACCOUNT_REFS_CEILING
from app.intelligence.proposal.contracts import MAX_PLAN_VERSION
from app.intelligence.proposal.contracts import MAX_PROFILE_TOKEN_CAP
from app.intelligence.proposal.contracts import MAX_SCHEMA_BYTES_CEILING
from app.intelligence.proposal.contracts import OBJECTIVE_REF_PATTERN
from app.intelligence.proposal.contracts import PROFILE_NAME_PATTERN
from app.intelligence.proposal.contracts import PROJECTION_CONTRACT
from app.intelligence.proposal.contracts import Exclusion
from app.intelligence.proposal.contracts import ExclusionReason
from app.intelligence.proposal.contracts import ProfileView
from app.intelligence.proposal.contracts import Projection
from app.intelligence.proposal.contracts import ProjectionLimits
from app.intelligence.proposal.contracts import ProjectionRejectionCode
from app.intelligence.proposal.contracts import ProjectionRequest
from app.intelligence.proposal.contracts import ProjectionResult
from app.intelligence.proposal.contracts import Rejection
from app.intelligence.proposal.contracts import make_rejection
from app.intelligence.proposal.digests import instructions_digest
from app.intelligence.proposal.digests import policy_digest
from app.intelligence.proposal.digests import projection_digest
from app.intelligence.proposal.digests import projection_material
from app.intelligence.proposal.digests import schema_digest
from app.intelligence.proposal.rendering import forbidden_identifiers
from app.intelligence.proposal.rendering import is_printable_ascii
from app.intelligence.proposal.rendering import render_schema
from app.intelligence.proposal.selection import PolicyUnusable
from app.intelligence.proposal.selection import select_capabilities


Code = ProjectionRejectionCode


class _Reject(Exception):
    """Internal control flow only; never leaves this module."""

    def __init__(self, *codes: ProjectionRejectionCode) -> None:
        super().__init__("rejected")
        self.codes = tuple(codes) or (Code.PROJECTION_INTERNAL_ERROR,)


def _failed(*codes: ProjectionRejectionCode) -> ProjectionResult:
    return ProjectionResult(
        None,
        tuple(sorted({make_rejection(code) for code in codes})),
    )


# ----------------------------------------------------------------------
# request validation (shape of OUR OWN contract; no foreign object read)
# ----------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return type(value) is int


def _is_text(value: Any) -> bool:
    return type(value) is str and is_printable_ascii(value)


def _validate_request(request: Any) -> tuple[ProjectionLimits, tuple[str, ...]]:
    if type(request) is not ProjectionRequest:
        raise _Reject(Code.REQUEST_INVALID)

    limits = request.limits
    profile = request.profile

    if (
        type(limits) is not ProjectionLimits
        or not _is_int(limits.max_schema_bytes)
        or not 1 <= limits.max_schema_bytes <= MAX_SCHEMA_BYTES_CEILING
        or not _is_int(limits.max_account_refs)
        or not 1 <= limits.max_account_refs <= MAX_ACCOUNT_REFS_CEILING
        or type(profile) is not ProfileView
        or not _is_text(profile.name)
        or not PROFILE_NAME_PATTERN.fullmatch(profile.name)
        or not _is_int(profile.max_output_tokens_cap)
        or not 1 <= profile.max_output_tokens_cap <= MAX_PROFILE_TOKEN_CAP
        or not _is_text(request.objective_ref)
        or not OBJECTIVE_REF_PATTERN.fullmatch(request.objective_ref)
        or not _is_int(request.plan_version)
        or not 1 <= request.plan_version <= MAX_PLAN_VERSION
        or not _is_text(request.pinned_registry_digest)
        or not DIGEST_PATTERN.fullmatch(request.pinned_registry_digest)
        or type(request.account_refs) is not tuple
        or not 1 <= len(request.account_refs) <= limits.max_account_refs
    ):
        raise _Reject(Code.REQUEST_INVALID)

    refs = request.account_refs

    if (
        not all(_is_text(ref) for ref in refs)
        or not all(ACCOUNT_REF_PATTERN.fullmatch(ref) for ref in refs)
        or len(set(refs)) != len(refs)
    ):
        raise _Reject(Code.REQUEST_INVALID)

    return limits, tuple(sorted(refs))


# ----------------------------------------------------------------------
# snapshots: foreign objects are read ONCE and copied; from here on only
# the copies are used
# ----------------------------------------------------------------------


def _plain_json(value: Any) -> Any:
    """
    A copy made of nothing but plain JSON values (dict, list, str, int,
    bool). It goes through the strict canonical form of the planning
    package, so unexpected types are refused rather than coerced, and
    the result shares no object with the original and keeps no custom
    subclass: whatever behaviour the original has, the copy has none.
    """
    return json.loads(canonical_bytes(value))


def _plain_limits(limits: PlanLimits) -> PlanLimits:
    values: dict[str, int] = {}

    for item in dataclasses.fields(PlanLimits):
        value = getattr(limits, item.name)

        if type(value) is not int:
            raise ValueError("limits")

        values[item.name] = value

    return PlanLimits(**values)


def _snapshot_registry(
    registry: Any,
    pinned_digest: str,
) -> CapabilityRegistry:
    try:
        if not isinstance(registry, CapabilityRegistry):
            raise ValueError("registry")

        definitions = tuple(registry.definitions)
        version = registry.version
        plan_limits = registry.limits

        if (
            type(version) is not str
            or type(plan_limits) is not PlanLimits
            or not all(
                isinstance(item, CapabilityDefinition)
                for item in definitions
            )
        ):
            raise ValueError("registry")

        copied = tuple(
            dataclasses.replace(
                item,
                input_schema=_plain_json(item.input_schema),
                output_schema=_plain_json(item.output_schema),
            )
            for item in definitions
        )
        snapshot = CapabilityRegistry(
            copied,
            version=version,
            limits=_plain_limits(plan_limits),
        )
    except Exception:
        raise _Reject(Code.REGISTRY_INTEGRITY_FAILURE) from None

    # The digest of the COPY is the one compared with the pin: the cached
    # digest of the original says nothing about what it holds now.
    if snapshot.digest != pinned_digest:
        raise _Reject(Code.REGISTRY_INTEGRITY_FAILURE)

    return snapshot


def _snapshot_authority(
    authority: Any,
) -> tuple[SyntheticAuthorityContext, str]:
    if not isinstance(authority, SyntheticAuthorityContext):
        raise _Reject(Code.AUTHORITY_CONTEXT_INVALID)

    try:
        # replace() runs the constructor checks again, including the
        # refusal in production.
        revalidated = dataclasses.replace(authority)
        accounts = {
            ref: AccountAccess(
                readable=_boolean(access.readable),
                manageable=_boolean(access.manageable),
            )
            for ref, access in dict(revalidated.accounts).items()
        }
        copied = SyntheticAuthorityContext(
            initiator_ref=revalidated.initiator_ref,
            role=revalidated.role,
            permissions=frozenset(revalidated.permissions),
            accounts=accounts,
            session_elevated=_boolean(revalidated.session_elevated),
            synthetic=True,
        )
        context = copied.context_digest()
    except Exception:
        raise _Reject(Code.AUTHORITY_CONTEXT_INVALID) from None

    return copied, context


def _boolean(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("Expected a boolean.")

    return value


def _snapshot_policy(policy: Any) -> tuple[PolicyReference, str]:
    if not isinstance(policy, PolicyReference):
        raise _Reject(Code.POLICY_REFERENCE_MISSING)

    try:
        copied = copy.deepcopy(policy)
        fingerprint = policy_digest(copied)
    except Exception:
        raise _Reject(Code.POLICY_REFERENCE_INVALID) from None

    return copied, fingerprint


# ----------------------------------------------------------------------
# project
# ----------------------------------------------------------------------


def _scope(
    authority: SyntheticAuthorityContext,
    refs: tuple[str, ...],
) -> tuple[bool, bool]:
    accesses = [authority.access_to(ref) for ref in refs]

    # One answer for "does not exist" and "is not yours".
    if any(access is None for access in accesses):
        raise _Reject(Code.SCOPE_INACCESSIBLE)

    return (
        all(access.readable for access in accesses),
        all(access.manageable for access in accesses),
    )


def _project(request: Any) -> ProjectionResult:
    limits, refs = _validate_request(request)
    registry = _snapshot_registry(
        request.registry, request.pinned_registry_digest
    )
    authority, context = _snapshot_authority(request.authority)
    policy, policy_fingerprint = _snapshot_policy(request.policy)
    all_readable, all_manageable = _scope(authority, refs)

    try:
        selection = select_capabilities(
            registry,
            authority,
            policy,
            all_readable=all_readable,
            all_manageable=all_manageable,
        )
    except PolicyUnusable:
        raise _Reject(Code.POLICY_REFERENCE_INVALID) from None

    if not selection.definitions:
        raise _Reject(Code.PROJECTION_EMPTY)

    forbidden = forbidden_identifiers(
        registry.definitions,
        selection.capability_ids,
        policy.known_permissions,
    )
    schema, outcome = render_schema(
        selection.definitions,
        registry,
        request.objective_ref,
        request.plan_version,
        limits.max_schema_bytes,
        forbidden,
    )

    if schema is None or outcome.schema_json is None:
        raise _Reject(*outcome.codes)

    schema_fingerprint = schema_digest(schema)
    instructions_fingerprint = instructions_digest(
        INSTRUCTIONS_TEMPLATE_ID, INSTRUCTIONS_TEXT
    )
    plan_limits = tuple(sorted(registry.limits.descriptor().items()))
    material = projection_material(
        contract=PROJECTION_CONTRACT,
        template_id=INSTRUCTIONS_TEMPLATE_ID,
        registry_digest=registry.digest,
        context_digest=context,
        policy_digest=policy_fingerprint,
        profile_name=request.profile.name,
        profile_max_output_tokens_cap=request.profile.max_output_tokens_cap,
        max_schema_bytes=limits.max_schema_bytes,
        max_account_refs=limits.max_account_refs,
        plan_limits=plan_limits,
        objective_ref=request.objective_ref,
        plan_version=request.plan_version,
        account_refs=refs,
        capability_ids=selection.capability_ids,
        exclusions=selection.exclusions,
        schema_digest=schema_fingerprint,
        instructions_digest=instructions_fingerprint,
    )

    return ProjectionResult(
        Projection(
            contract=PROJECTION_CONTRACT,
            template_id=INSTRUCTIONS_TEMPLATE_ID,
            registry_digest=registry.digest,
            context_digest=context,
            policy_digest=policy_fingerprint,
            profile_name=request.profile.name,
            profile_max_output_tokens_cap=(
                request.profile.max_output_tokens_cap
            ),
            max_schema_bytes=limits.max_schema_bytes,
            max_account_refs=limits.max_account_refs,
            plan_limits=plan_limits,
            objective_ref=request.objective_ref,
            plan_version=request.plan_version,
            account_refs=refs,
            capability_ids=selection.capability_ids,
            exclusions=selection.exclusions,
            output_schema_json=outcome.schema_json,
            schema_bytes=outcome.schema_bytes,
            schema_digest=schema_fingerprint,
            instructions=INSTRUCTIONS_TEXT,
            instructions_digest=instructions_fingerprint,
            projection_digest=projection_digest(material),
        )
    )


def project(request: Any) -> ProjectionResult:
    """
    Total: it never raises an Exception. A hostile or malformed input
    is a typed rejection; an unexpected internal failure is reported as
    PROJECTION_INTERNAL_ERROR (tests treat that code as a defect).
    """
    try:
        return _project(request)
    except _Reject as rejected:
        return _failed(*rejected.codes)
    except Exception:
        return _failed(Code.PROJECTION_INTERNAL_ERROR)


# ----------------------------------------------------------------------
# verify_projection
# ----------------------------------------------------------------------

_TEXT_FIELDS = (
    "contract",
    "template_id",
    "profile_name",
    "objective_ref",
    "output_schema_json",
    "instructions",
)
_DIGEST_FIELDS = (
    "registry_digest",
    "context_digest",
    "policy_digest",
    "schema_digest",
    "instructions_digest",
    "projection_digest",
)
_INT_FIELDS = (
    "profile_max_output_tokens_cap",
    "max_schema_bytes",
    "max_account_refs",
    "plan_version",
    "schema_bytes",
)


def _well_formed(projection: Any) -> bool:
    if type(projection) is not Projection:
        return False

    names = {field.name for field in dataclasses.fields(Projection)}

    if set(vars(projection)) != names:
        return False

    if not all(_is_text(getattr(projection, name)) for name in _TEXT_FIELDS):
        return False

    if not all(
        _is_text(getattr(projection, name))
        and DIGEST_PATTERN.fullmatch(getattr(projection, name))
        for name in _DIGEST_FIELDS
    ):
        return False

    if not all(_is_int(getattr(projection, name)) for name in _INT_FIELDS):
        return False

    if (
        projection.synthetic_authority is not True
        or projection.is_grant is not False
        or projection.replay_protected is not False
    ):
        return False

    if (
        type(projection.account_refs) is not tuple
        or type(projection.capability_ids) is not tuple
        or type(projection.exclusions) is not tuple
        or type(projection.plan_limits) is not tuple
        or not all(_is_text(item) for item in projection.account_refs)
        or not all(_is_text(item) for item in projection.capability_ids)
    ):
        return False

    if not all(
        type(item) is Exclusion
        and _is_text(item.capability_id)
        and type(item.reason) is ExclusionReason
        for item in projection.exclusions
    ):
        return False

    return all(
        type(pair) is tuple
        and len(pair) == 2
        and _is_text(pair[0])
        and _is_int(pair[1])
        for pair in projection.plan_limits
    )


def _self_consistent(projection: Projection) -> bool:
    schema = json.loads(projection.output_schema_json)
    canonical = canonical_bytes(schema).decode("ascii")

    if (
        canonical != projection.output_schema_json
        or len(canonical) != projection.schema_bytes
        or schema_digest(schema) != projection.schema_digest
        or projection.instructions != INSTRUCTIONS_TEXT
        or projection.template_id != INSTRUCTIONS_TEMPLATE_ID
        or projection.contract != PROJECTION_CONTRACT
        or instructions_digest(projection.template_id, projection.instructions)
        != projection.instructions_digest
    ):
        return False

    material = projection_material(
        contract=projection.contract,
        template_id=projection.template_id,
        registry_digest=projection.registry_digest,
        context_digest=projection.context_digest,
        policy_digest=projection.policy_digest,
        profile_name=projection.profile_name,
        profile_max_output_tokens_cap=projection.profile_max_output_tokens_cap,
        max_schema_bytes=projection.max_schema_bytes,
        max_account_refs=projection.max_account_refs,
        plan_limits=projection.plan_limits,
        objective_ref=projection.objective_ref,
        plan_version=projection.plan_version,
        account_refs=projection.account_refs,
        capability_ids=projection.capability_ids,
        exclusions=projection.exclusions,
        schema_digest=projection.schema_digest,
        instructions_digest=projection.instructions_digest,
    )

    return projection_digest(material) == projection.projection_digest


def verify_projection(
    projection: Any,
    request: Any,
) -> tuple[Rejection, ...]:
    """
    Total. An empty tuple means: well formed, internally consistent AND
    exactly what the given request produces now. Otherwise exactly one
    code: MALFORMED (shape or types), TAMPERED (its own digests do not
    match its own content) or STALE (consistent, but the request no
    longer yields it: authority, policy, registry or scope changed).
    """
    try:
        if not _well_formed(projection):
            return (make_rejection(Code.PROJECTION_MALFORMED),)
    except Exception:
        return (make_rejection(Code.PROJECTION_MALFORMED),)

    try:
        consistent = _self_consistent(projection)
    except Exception:
        consistent = False

    if not consistent:
        return (make_rejection(Code.PROJECTION_TAMPERED),)

    try:
        fresh = project(request)
        same = fresh.projection is not None and fresh.projection == projection
    except Exception:
        same = False

    if not same:
        return (make_rejection(Code.PROJECTION_STALE),)

    return ()
