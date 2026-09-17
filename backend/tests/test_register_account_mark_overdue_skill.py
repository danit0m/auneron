"""
P1.2A -- prova de idempotencia do registro de catalogo de
account.mark_overdue. So o mecanismo de registro (Skill/SkillVersion/
capability via SkillService) -- nenhum endpoint, ApprovalRequest,
corredor 25M/25O ou AgentSkillBinding e tocado por este teste.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from scripts.register_account_mark_overdue_skill import (
    CAPABILITY_KEY,
)
from scripts.register_account_mark_overdue_skill import (
    HANDLER_REFERENCE,
)
from scripts.register_account_mark_overdue_skill import PROVIDER
from scripts.register_account_mark_overdue_skill import SKILL_KEY
from scripts.register_account_mark_overdue_skill import main


def _counts(db_session: Session) -> dict[str, int]:
    return {
        "skills": db_session.execute(
            text(
                "SELECT COUNT(*) FROM skills "
                "WHERE skill_key = :key"
            ),
            {"key": SKILL_KEY},
        ).scalar_one(),
        "versions": db_session.execute(
            text(
                "SELECT COUNT(*) FROM skill_versions sv "
                "JOIN skills s ON s.id = sv.skill_id "
                "WHERE s.skill_key = :key"
            ),
            {"key": SKILL_KEY},
        ).scalar_one(),
        "capabilities": db_session.execute(
            text(
                "SELECT COUNT(*) FROM skill_capabilities sc "
                "JOIN skill_versions sv "
                "ON sv.id = sc.skill_version_id "
                "JOIN skills s ON s.id = sv.skill_id "
                "WHERE s.skill_key = :key"
            ),
            {"key": SKILL_KEY},
        ).scalar_one(),
        "bindings": db_session.execute(
            text(
                "SELECT COUNT(*) FROM agent_skill_bindings b "
                "JOIN skill_versions sv "
                "ON sv.id = b.skill_version_id "
                "JOIN skills s ON s.id = sv.skill_id "
                "WHERE s.skill_key = :key"
            ),
            {"key": SKILL_KEY},
        ).scalar_one(),
    }


def test_first_run_registers_exactly_one_published_skill_version(
    db_session: Session,
) -> None:
    main()

    counts = _counts(db_session)
    assert counts == {
        "skills": 1,
        "versions": 1,
        "capabilities": 1,
        "bindings": 0,
    }

    row = db_session.execute(
        text(
            "SELECT provider, status FROM skills "
            "WHERE skill_key = :key"
        ),
        {"key": SKILL_KEY},
    ).one()
    assert row.provider == PROVIDER
    assert row.status == "active"

    version_row = db_session.execute(
        text(
            "SELECT sv.status, sv.execution_mode, "
            "sv.handler_reference, sv.runtime_kind "
            "FROM skill_versions sv "
            "JOIN skills s ON s.id = sv.skill_id "
            "WHERE s.skill_key = :key"
        ),
        {"key": SKILL_KEY},
    ).one()
    assert version_row.status == "published"
    assert version_row.execution_mode == "mutating"
    assert version_row.handler_reference == HANDLER_REFERENCE
    assert version_row.runtime_kind == "internal_python"

    capability_row = db_session.execute(
        text(
            "SELECT sc.capability_key, sc.access_mode, "
            "sc.resource_scope FROM skill_capabilities sc "
            "JOIN skill_versions sv "
            "ON sv.id = sc.skill_version_id "
            "JOIN skills s ON s.id = sv.skill_id "
            "WHERE s.skill_key = :key"
        ),
        {"key": SKILL_KEY},
    ).one()
    assert capability_row.capability_key == CAPABILITY_KEY
    assert capability_row.access_mode == "write"
    assert capability_row.resource_scope == "account"


def test_second_run_converges_without_duplicating_or_raising(
    db_session: Session,
) -> None:
    main()
    first_counts = _counts(db_session)

    main()  # segunda execução -- não deve levantar nem duplicar
    second_counts = _counts(db_session)

    assert first_counts == second_counts
    assert second_counts == {
        "skills": 1,
        "versions": 1,
        "capabilities": 1,
        "bindings": 0,
    }
