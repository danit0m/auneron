from datetime import datetime
from datetime import timedelta
from datetime import timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.approval_errors import ApprovalAuthorizationError
from app.core.approval_errors import ApprovalConflictError
from app.core.approval_errors import ApprovalNotFoundError
from app.core.approval_errors import ApprovalStateError
from app.core.policy_definitions import ACCOUNT_MARK_OVERDUE_POLICY_V1
from app.models.policy_authority_grant import PolicyAuthorityGrant
from app.models.user import User
from app.services.policy_authority_grant_service import (
    PolicyAuthorityGrantService,
)


def _user(
    db_session: Session,
    *,
    email: str,
    role: str,
) -> User:
    user = User(
        name="Policy Grant Test",
        email=email,
        password_hash="not-used",
        role=role,
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _future(hours: int = 24) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=hours)


def test_grant_creation_persists_active_state_with_deployment_wide_scope(
    db_session: Session,
) -> None:
    granter = _user(
        db_session, email="granter1@example.com", role="administrator"
    )
    service = PolicyAuthorityGrantService(db_session)

    result = service.create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=_future(),
    )

    assert result.grant.state == "active"
    assert result.grant.scope_type == "deployment_wide"
    assert result.grant.skill_key == "account.mark_overdue"
    assert (
        result.grant.policy_version
        == ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_version
    )
    assert result.grant.granted_by_user_id == granter.id
    assert result.grant.granted_by_reference == f"user:{granter.id}"


def test_grant_creation_rejects_system_as_granted_by_role(
    db_session: Session,
) -> None:
    granter = _user(
        db_session, email="granter-system@example.com", role="system"
    )
    service = PolicyAuthorityGrantService(db_session)

    with pytest.raises(ApprovalAuthorizationError):
        service.create_grant(
            policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
            granted_by_user_id=granter.id,
            expires_at=_future(),
        )


def test_grant_creation_rejects_unknown_policy_key_or_version(
    db_session: Session,
) -> None:
    granter = _user(
        db_session, email="granter2@example.com", role="administrator"
    )
    service = PolicyAuthorityGrantService(db_session)

    with pytest.raises(ApprovalNotFoundError):
        service.create_grant(
            policy_key="policy.unknown.v1",
            granted_by_user_id=granter.id,
            expires_at=_future(),
        )


def test_grant_creation_rejects_skill_key_other_than_mark_overdue(
    db_session: Session,
) -> None:
    granter = _user(
        db_session, email="granter3@example.com", role="administrator"
    )
    # Nenhum caminho via serviço permite propor outro skill_key -- a
    # Policy Definition fixa isso. Prova a constraint de banco
    # diretamente por inserção de modelo, mesmo padrão já usado neste
    # repositório para provar CHECKs sem caminho de aplicação real.
    grant = PolicyAuthorityGrant(
        policy_key="policy.account_mark_overdue.v1",
        policy_version="1",
        skill_key="account.mark_paid",
        scope_type="deployment_wide",
        state="active",
        granted_by_user_id=granter.id,
        granted_by_reference=f"user:{granter.id}",
        granted_by_role="administrator",
        valid_from=datetime.now(timezone.utc),
        expires_at=_future(),
    )
    db_session.add(grant)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_second_active_grant_for_same_policy_and_skill_conflicts(
    db_session: Session,
) -> None:
    granter = _user(
        db_session, email="granter4@example.com", role="administrator"
    )
    service = PolicyAuthorityGrantService(db_session)
    service.create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=_future(),
    )

    with pytest.raises(ApprovalConflictError):
        service.create_grant(
            policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
            granted_by_user_id=granter.id,
            expires_at=_future(hours=48),
        )


def test_grant_revocation_transitions_state_and_sets_provenance(
    db_session: Session,
) -> None:
    granter = _user(
        db_session, email="granter5@example.com", role="administrator"
    )
    revoker = _user(
        db_session, email="revoker5@example.com", role="administrator"
    )
    service = PolicyAuthorityGrantService(db_session)
    created = service.create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=_future(),
    )

    revoked = service.revoke_grant(
        created.grant.id, revoked_by_user_id=revoker.id
    )

    assert revoked.grant.state == "revoked"
    assert revoked.grant.revoked_at is not None
    assert revoked.grant.revoked_by_user_id == revoker.id
    assert revoked.grant.revoked_by_reference == f"user:{revoker.id}"


def test_already_revoked_grant_cannot_be_revoked_again(
    db_session: Session,
) -> None:
    granter = _user(
        db_session, email="granter6@example.com", role="administrator"
    )
    revoker = _user(
        db_session, email="revoker6@example.com", role="administrator"
    )
    service = PolicyAuthorityGrantService(db_session)
    created = service.create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=_future(),
    )
    service.revoke_grant(
        created.grant.id, revoked_by_user_id=revoker.id
    )

    with pytest.raises(ApprovalStateError):
        service.revoke_grant(
            created.grant.id, revoked_by_user_id=revoker.id
        )


def test_expired_grant_fails_closed_at_consumption_without_background_job(
    db_session: Session,
) -> None:
    """
    Nenhum job de manutenção transiciona `state` para 'expired' -- a
    rejeição em tempo de consumo é feita puramente via `expires_at`
    (ver test_policy_account_mark_overdue_execution.py::
    test_execution_fails_closed_when_grant_expired`). Aqui provamos a
    metade "sem background job": mesmo com `expires_at` já no passado
    em relógio real, a linha persistida continua com `state='active'`.
    """
    granter = _user(
        db_session, email="granter7@example.com", role="administrator"
    )
    service = PolicyAuthorityGrantService(db_session)
    backdated_now = datetime.now(timezone.utc) - timedelta(hours=2)
    created = service.create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=backdated_now + timedelta(hours=1),
        now=backdated_now,
    )

    db_session.expire_all()
    reloaded = db_session.get(
        PolicyAuthorityGrant, created.grant.id
    )
    assert reloaded.state == "active"
    assert reloaded.expires_at < datetime.now(timezone.utc)


def test_grant_audit_survives_granting_user_deletion(
    db_session: Session,
) -> None:
    granter = _user(
        db_session, email="granter8@example.com", role="administrator"
    )
    service = PolicyAuthorityGrantService(db_session)
    created = service.create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=_future(),
    )
    grant_id = created.grant.id
    granted_by_reference = created.grant.granted_by_reference

    db_session.delete(db_session.get(User, granter.id))
    db_session.commit()

    db_session.expire_all()
    reloaded = db_session.get(PolicyAuthorityGrant, grant_id)
    assert reloaded.granted_by_user_id is None
    assert reloaded.granted_by_reference == granted_by_reference
    assert reloaded.state == "active"


def test_revoked_grant_audit_survives_revoking_user_deletion(
    db_session: Session,
) -> None:
    granter = _user(
        db_session, email="granter9@example.com", role="administrator"
    )
    revoker = _user(
        db_session, email="revoker9@example.com", role="administrator"
    )
    service = PolicyAuthorityGrantService(db_session)
    created = service.create_grant(
        policy_key=ACCOUNT_MARK_OVERDUE_POLICY_V1.policy_key,
        granted_by_user_id=granter.id,
        expires_at=_future(),
    )
    revoked = service.revoke_grant(
        created.grant.id, revoked_by_user_id=revoker.id
    )
    grant_id = revoked.grant.id
    revoked_by_reference = revoked.grant.revoked_by_reference

    db_session.delete(db_session.get(User, revoker.id))
    db_session.commit()

    db_session.expire_all()
    reloaded = db_session.get(PolicyAuthorityGrant, grant_id)
    assert reloaded.revoked_by_user_id is None
    assert reloaded.revoked_by_reference == revoked_by_reference
    assert reloaded.state == "revoked"
