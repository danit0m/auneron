"""
Testes do script de vinculo da deteccao de vencidos
(scripts/create_overdue_detection_binding.py): recusa banco diferente do
esperado e L3 com autoridade ativa, exige exatamente uma versao elegivel,
e vincula/desvincula de forma idempotente, sem nunca apagar binding.
"""

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from sqlalchemy import text

from app.core.authentication import hash_password
from app.models.policy_authority_grant import PolicyAuthorityGrant
from app.models.skill import AgentSkillBinding
from app.models.skill import SkillDefinition
from app.models.skill import SkillVersion
from app.models.user import User
from app.services.overdue_detection_service import OVERDUE_AGENT_NAME
from scripts import create_overdue_detection_binding as script

MANIFEST_DIGEST = (
    "9dcee461a5606d7def14d8884e06aac5415ca76d19388acbc7b1e77c96fa1b79"
)


def _database_name(db_session) -> str:
    return db_session.execute(text("SELECT current_database()")).scalar()


def _make_user(db_session) -> User:
    user = User(
        name="Admin binding",
        email="admin.binding@example.com",
        password_hash=hash_password("Senha-Teste-Auneron-123!"),
        role="administrator",
        active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_skill(db_session, *, status: str = "active") -> SkillDefinition:
    skill = SkillDefinition(
        skill_key="account.mark_overdue",
        provider="auneron.core",
        display_name="Marcar conta como atrasada",
        description="Governed pilot action account.mark_overdue.",
        status=status,
    )
    db_session.add(skill)
    db_session.commit()
    db_session.refresh(skill)
    return skill


def _make_version(
    db_session,
    skill,
    *,
    version: str = "1.0.0",
    digest: str = MANIFEST_DIGEST,
    execution_mode: str = "mutating",
    runtime_kind: str = "internal_python",
    published: bool = True,
) -> SkillVersion:
    row = SkillVersion(
        skill_id=skill.id,
        version=version,
        runtime_kind=runtime_kind,
        handler_reference="app.skills.account:mark_overdue",
        execution_mode=execution_mode,
        manifest_digest=digest,
        status="published" if published else "draft",
        published_at=datetime.now(timezone.utc) if published else None,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _make_grant(
    db_session, granter, *, state="active", expires_in_hours=24
) -> PolicyAuthorityGrant:
    now = datetime.now(timezone.utc)
    revoked = state == "revoked"
    grant = PolicyAuthorityGrant(
        policy_key="policy.account_mark_overdue.v1",
        policy_version="1",
        skill_key="account.mark_overdue",
        scope_type="deployment_wide",
        state=state,
        granted_by_user_id=granter.id,
        granted_by_reference=f"user:{granter.id}",
        granted_by_role="administrator",
        valid_from=now - timedelta(hours=48),
        expires_at=now + timedelta(hours=expires_in_hours),
        revoked_at=now if revoked else None,
        revoked_by_user_id=granter.id if revoked else None,
        revoked_by_reference=f"user:{granter.id}" if revoked else None,
    )
    db_session.add(grant)
    db_session.commit()
    db_session.refresh(grant)
    return grant


def _bindings(db_session) -> list[AgentSkillBinding]:
    db_session.expire_all()
    return (
        db_session.query(AgentSkillBinding)
        .filter(AgentSkillBinding.agent_name == OVERDUE_AGENT_NAME)
        .order_by(AgentSkillBinding.id)
        .all()
    )


def _add_binding(db_session, version, *, enabled: bool) -> AgentSkillBinding:
    binding = AgentSkillBinding(
        agent_name=OVERDUE_AGENT_NAME,
        skill_version_id=version.id,
        priority=100,
        enabled=enabled,
    )
    db_session.add(binding)
    db_session.commit()
    db_session.refresh(binding)
    return binding


def test_default_expected_database_is_synthetic_and_not_test_db(db_session):
    assert script.EXPECTED_DATABASE == "auneron_neon_synthetic"
    assert _database_name(db_session) != script.EXPECTED_DATABASE
    assert script.run(db_session) == script.EXIT_WRONG_DATABASE


def test_wrong_database_exits_4_without_writing(db_session, capsys):
    skill = _make_skill(db_session)
    _make_version(db_session, skill)

    code = script.run(db_session, script.MODE_BIND, "outro_banco_qualquer")
    out = capsys.readouterr().out

    assert code == script.EXIT_WRONG_DATABASE == 4
    assert "RECUSADO" in out
    assert _bindings(db_session) == []


def test_wrong_database_does_not_unbind(db_session):
    skill = _make_skill(db_session)
    version = _make_version(db_session, skill)
    binding = _add_binding(db_session, version, enabled=True)

    code = script.run(db_session, script.MODE_UNBIND, "outro_banco_qualquer")

    assert code == script.EXIT_WRONG_DATABASE
    assert _bindings(db_session)[0].id == binding.id
    assert _bindings(db_session)[0].enabled is True


def test_active_grant_exits_5_without_writing(db_session, capsys):
    skill = _make_skill(db_session)
    _make_version(db_session, skill)
    _make_grant(db_session, _make_user(db_session))

    code = script.run(db_session, script.MODE_BIND, _database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_L3_AUTHORITY_ACTIVE == 5
    assert "L3 com autoridade ativa" in out
    assert _bindings(db_session) == []


def test_revoked_grant_does_not_block(db_session):
    skill = _make_skill(db_session)
    _make_version(db_session, skill)
    _make_grant(db_session, _make_user(db_session), state="revoked")

    code = script.run(db_session, script.MODE_BIND, _database_name(db_session))

    assert code == script.EXIT_OK
    assert len(_bindings(db_session)) == 1


def test_no_skill_exits_3(db_session, capsys):
    code = script.run(db_session, script.MODE_BIND, _database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_INELIGIBLE == 3
    assert "INELEGIVEL" in out
    assert _bindings(db_session) == []


def test_inactive_skill_exits_3(db_session):
    skill = _make_skill(db_session, status="disabled")
    _make_version(db_session, skill)

    code = script.run(db_session, script.MODE_BIND, _database_name(db_session))

    assert code == script.EXIT_INELIGIBLE
    assert _bindings(db_session) == []


def test_no_eligible_version_exits_3(db_session):
    skill = _make_skill(db_session)
    _make_version(db_session, skill, execution_mode="read_only")
    _make_version(
        db_session,
        skill,
        version="1.1.0",
        digest="a" * 64,
        published=False,
    )

    code = script.run(db_session, script.MODE_BIND, _database_name(db_session))

    assert code == script.EXIT_INELIGIBLE
    assert _bindings(db_session) == []


def test_two_eligible_versions_exits_3(db_session):
    skill = _make_skill(db_session)
    _make_version(db_session, skill)
    _make_version(db_session, skill, version="2.0.0", digest="b" * 64)

    code = script.run(db_session, script.MODE_BIND, _database_name(db_session))

    assert code == script.EXIT_INELIGIBLE
    assert _bindings(db_session) == []


def test_binds_enabled_with_priority_100(db_session, capsys):
    skill = _make_skill(db_session)
    version = _make_version(db_session, skill)

    code = script.run(db_session, script.MODE_BIND, _database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    bindings = _bindings(db_session)
    assert len(bindings) == 1
    assert bindings[0].skill_version_id == version.id
    assert bindings[0].enabled is True
    assert bindings[0].priority == 100
    assert "vinculado agora" in out
    assert f"binding_id      : {bindings[0].id}" in out
    assert f"skill_version_id: {version.id}" in out
    assert OVERDUE_AGENT_NAME in out


def test_bind_is_idempotent_when_already_enabled(db_session, capsys):
    skill = _make_skill(db_session)
    version = _make_version(db_session, skill)
    existing = _add_binding(db_session, version, enabled=True)

    code = script.run(db_session, script.MODE_BIND, _database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    assert "ja vinculado" in out
    bindings = _bindings(db_session)
    assert [b.id for b in bindings] == [existing.id]
    assert bindings[0].enabled is True


def test_existing_disabled_binding_is_not_changed(db_session, capsys):
    skill = _make_skill(db_session)
    version = _make_version(db_session, skill)
    existing = _add_binding(db_session, version, enabled=False)

    code = script.run(db_session, script.MODE_BIND, _database_name(db_session))
    out = capsys.readouterr().out

    assert code == script.EXIT_INELIGIBLE == 3
    assert "desabilitado" in out
    bindings = _bindings(db_session)
    assert [b.id for b in bindings] == [existing.id]
    assert bindings[0].enabled is False


def test_unbind_disables_without_deleting(db_session, capsys):
    skill = _make_skill(db_session)
    version = _make_version(db_session, skill)
    existing = _add_binding(db_session, version, enabled=True)

    code = script.run(
        db_session, script.MODE_UNBIND, _database_name(db_session)
    )
    out = capsys.readouterr().out

    assert code == script.EXIT_OK
    assert "desabilitado" in out
    bindings = _bindings(db_session)
    assert [b.id for b in bindings] == [existing.id]
    assert bindings[0].enabled is False


def test_unbind_when_already_disabled_or_absent_exits_0(db_session):
    skill = _make_skill(db_session)
    version = _make_version(db_session, skill)
    database = _database_name(db_session)

    assert script.run(db_session, script.MODE_UNBIND, database) == 0
    assert _bindings(db_session) == []

    existing = _add_binding(db_session, version, enabled=False)
    assert script.run(db_session, script.MODE_UNBIND, database) == 0
    bindings = _bindings(db_session)
    assert [b.id for b in bindings] == [existing.id]
    assert bindings[0].enabled is False


def test_unbind_is_allowed_with_active_grant(db_session):
    skill = _make_skill(db_session)
    version = _make_version(db_session, skill)
    _add_binding(db_session, version, enabled=True)
    _make_grant(db_session, _make_user(db_session))

    code = script.run(
        db_session, script.MODE_UNBIND, _database_name(db_session)
    )

    assert code == script.EXIT_OK
    assert _bindings(db_session)[0].enabled is False
