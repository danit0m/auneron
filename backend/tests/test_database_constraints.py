import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


ACCOUNT_INSERT = text(
    """
    INSERT INTO accounts (
        cliente,
        valor,
        vencimento,
        status
    )
    VALUES (
        :cliente,
        :valor,
        :vencimento,
        :status
    )
    """
)


def assert_integrity_error(
    db_session: Session,
    statement,
    parameters: dict,
) -> None:
    with pytest.raises(IntegrityError):
        db_session.execute(
            statement,
            parameters,
        )
        db_session.commit()

    db_session.rollback()


def test_database_rejects_invalid_account_status(
    db_session: Session,
) -> None:
    assert_integrity_error(
        db_session,
        ACCOUNT_INSERT,
        {
            "cliente": "Cliente Status",
            "valor": 1000.00,
            "vencimento": "2026-12-31",
            "status": "pendente",
        },
    )


def test_database_rejects_zero_account_value(
    db_session: Session,
) -> None:
    assert_integrity_error(
        db_session,
        ACCOUNT_INSERT,
        {
            "cliente": "Cliente Valor",
            "valor": 0,
            "vencimento": "2026-12-31",
            "status": "aberto",
        },
    )


def test_database_rejects_blank_account_name(
    db_session: Session,
) -> None:
    assert_integrity_error(
        db_session,
        ACCOUNT_INSERT,
        {
            "cliente": " ",
            "valor": 1000.00,
            "vencimento": "2026-12-31",
            "status": "aberto",
        },
    )


def test_database_rejects_invalid_knowledge_severity(
    db_session: Session,
) -> None:
    statement = text(
        """
        INSERT INTO knowledge (
            agent_name,
            event_name,
            knowledge_type,
            severity,
            title,
            message
        )
        VALUES (
            :agent_name,
            :event_name,
            :knowledge_type,
            :severity,
            :title,
            :message
        )
        """
    )

    assert_integrity_error(
        db_session,
        statement,
        {
            "agent_name": "TestAgent",
            "event_name": "evento_teste",
            "knowledge_type": "insight",
            "severity": "warning",
            "title": "Conhecimento inválido",
            "message": "Teste de constraint do banco.",
        },
    )
