import ast
from dataclasses import FrozenInstanceError
from dataclasses import fields
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest

from app.core.authentication import AuthenticatedSession
from app.core.authority_provenance import (
    AUTHORITY_PROVENANCE_REQUEST_ID_MAX_LENGTH,
)
from app.core.authority_provenance import (
    AUTHORITY_PROVENANCE_SOURCE,
)
from app.core.authority_provenance import AuthorityProvenance
from app.core.authority_provenance import (
    authority_provenance_from_authenticated_session,
)
from app.models.auth_session import AuthSession
from app.models.user import User


def _authenticated_session(
    *,
    user_id: int = 11,
    session_id: int = 22,
    session_user_id: int | None = None,
) -> AuthenticatedSession:
    user = User(
        id=user_id,
        name="Authority Test",
        email="authority@example.com",
        password_hash="not-used",
        role="manager",
        active=True,
    )
    session = AuthSession(
        id=session_id,
        user_id=(
            user_id
            if session_user_id is None
            else session_user_id
        ),
        token_hash="a" * 64,
        expires_at=(
            datetime.now(timezone.utc)
            + timedelta(hours=1)
        ),
    )
    return AuthenticatedSession(
        user=user,
        session=session,
    )


def test_authority_provenance_is_immutable_and_server_derived() -> None:
    authenticated = _authenticated_session()

    provenance = authority_provenance_from_authenticated_session(
        authenticated,
        request_id=" req-25g ",
    )

    assert provenance.authority_user_id == 11
    assert provenance.auth_session_id == 22
    assert provenance.request_id == "req-25g"

    with pytest.raises(FrozenInstanceError):
        provenance.authority_user_id = 99


def test_authority_user_id_must_be_positive_int() -> None:
    for invalid in (
        0,
        -1,
        True,
        "11",
    ):
        authenticated = _authenticated_session(
            user_id=invalid,
        )

        with pytest.raises(
            (TypeError, ValueError),
        ):
            authority_provenance_from_authenticated_session(
                authenticated
            )


def test_auth_session_id_must_be_positive_int_and_match_user() -> None:
    for invalid in (
        0,
        -1,
        True,
        "22",
    ):
        authenticated = _authenticated_session(
            session_id=invalid,
        )

        with pytest.raises(
            (TypeError, ValueError),
        ):
            authority_provenance_from_authenticated_session(
                authenticated
            )

    mismatched = _authenticated_session(
        user_id=11,
        session_id=22,
        session_user_id=12,
    )

    with pytest.raises(ValueError):
        authority_provenance_from_authenticated_session(
            mismatched
        )


def test_request_id_is_optional_bounded_server_metadata() -> None:
    authenticated = _authenticated_session()

    assert (
        authority_provenance_from_authenticated_session(
            authenticated
        ).request_id
        is None
    )

    maximum = "r" * (
        AUTHORITY_PROVENANCE_REQUEST_ID_MAX_LENGTH
    )

    assert (
        authority_provenance_from_authenticated_session(
            authenticated,
            request_id=maximum,
        ).request_id
        == maximum
    )

    for invalid in (
        "",
        "   ",
        "r"
        * (
            AUTHORITY_PROVENANCE_REQUEST_ID_MAX_LENGTH
            + 1
        ),
    ):
        with pytest.raises(ValueError):
            authority_provenance_from_authenticated_session(
                authenticated,
                request_id=invalid,
            )

    with pytest.raises(TypeError):
        authority_provenance_from_authenticated_session(
            authenticated,
            request_id=123,
        )


def test_source_is_fixed_authenticated_http_session() -> None:
    provenance = authority_provenance_from_authenticated_session(
        _authenticated_session()
    )

    assert (
        provenance.source
        == AUTHORITY_PROVENANCE_SOURCE
        == "authenticated_http_session"
    )

    with pytest.raises(ValueError):
        AuthorityProvenance(
            authority_user_id=11,
            auth_session_id=22,
            source="client",
        )


def test_reference_has_no_role_permission_scope_or_elevation_fields() -> None:
    field_names = {
        field.name
        for field in fields(
            AuthorityProvenance
        )
    }

    assert field_names == {
        "authority_user_id",
        "auth_session_id",
        "request_id",
        "source",
    }

    for forbidden in (
        "role",
        "permissions",
        "account_id",
        "subject_user_id",
        "scope_type",
        "session_elevated",
        "elevated_until",
        "approval_request_id",
    ):
        assert forbidden not in field_names


def test_reference_has_no_payload_token_or_credential_fields() -> None:
    field_names = {
        field.name
        for field in fields(
            AuthorityProvenance
        )
    }

    for forbidden in (
        "skill_version_id",
        "binding_id",
        "input_payload",
        "runtime_context",
        "memory",
        "credentials",
        "tokens",
        "token",
        "password",
    ):
        assert forbidden not in field_names


def test_reference_exposes_no_authorize_execute_or_dispatch_method() -> None:
    for forbidden in (
        "authorize",
        "execute",
        "dispatch",
        "publish",
        "create_work",
    ):
        assert not hasattr(
            AuthorityProvenance,
            forbidden,
        )


def test_module_imports_no_execution_or_mutation_dependencies() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "core"
        / "authority_provenance.py"
    )
    tree = ast.parse(
        source_path.read_text(
            encoding="utf-8",
        )
    )

    forbidden_modules = {
        "app.agents.event_bus",
        "app.orchestrator.orchestrator",
        "app.services.orchestrator_skill_binding_projection",
        "app.services.work_service",
        "app.services.work_skill_execution",
        "app.services.governed_skill_execution",
        "app.services.approval_service",
        "app.services.skill_runtime",
        "app.services.memory_service",
    }

    imported_modules = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(
                alias.name
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported_modules.add(
                    node.module
                )

    assert not (
        imported_modules
        & forbidden_modules
    )


def test_future_reauthorization_contract_is_documented_fail_closed() -> None:
    doc_path = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "orchestrator"
        / "AUTHORITY_PROVENANCE.md"
    )
    normalized = " ".join(
        doc_path.read_text(
            encoding="utf-8",
        )
        .lower()
        .split()
    )

    for required in (
        "reload the current user",
        "reload the current auth session",
        "validate that the session is still active",
        "recalculate current role and permissions",
        "reauthorize current scope",
        "reauthorize the exact skill",
        "fail closed",
        "no production eventbus wiring",
    ):
        assert required in normalized
