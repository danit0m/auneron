from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import func
from sqlalchemy import text

from app.database.database import Base


class PolicyAuthorityGrant(Base):
    """
    DW-7.3 V1 -- concessao ex-ante, persistida e revogavel, de
    autoridade delegada a uma Policy Definition (constante de codigo,
    ver app/core/policy_definitions.py) sobre um skill_key exato.

    Authority != Actor (DW-7.1 Q1): este grant nunca executa nada -- ele
    e consumido por app/services/policy_account_mark_overdue_execution_
    service.py, que le esta tabela mas nunca escreve nela (L3-A,
    imposto tambem por
    tests/test_policy_execution_service_has_no_grant_mutation_dependency.py).
    """

    __tablename__ = "policy_authority_grants"

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(policy_key)) >= 1",
            name="ck_policy_authority_grants_policy_key_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(policy_version)) >= 1",
            name="ck_policy_authority_grants_policy_version_not_blank",
        ),
        CheckConstraint(
            "skill_key IN ('account.mark_overdue')",
            name="ck_policy_authority_grants_skill_key_valid",
        ),
        CheckConstraint(
            "scope_type IN ('deployment_wide')",
            name="ck_policy_authority_grants_scope_type_valid",
        ),
        CheckConstraint(
            "state IN ('active', 'revoked', 'expired')",
            name="ck_policy_authority_grants_state_valid",
        ),
        CheckConstraint(
            "char_length(btrim(granted_by_reference)) >= 1",
            name="ck_policy_authority_grants_granted_by_ref_not_blank",
        ),
        CheckConstraint(
            "granted_by_role IN ("
            "'viewer', 'analyst', 'manager', 'executive', "
            "'administrator', 'developer'"
            ")",
            name="ck_policy_authority_grants_granted_by_role_valid",
        ),
        CheckConstraint(
            "expires_at > valid_from",
            name="ck_policy_authority_grants_expiration_order",
        ),
        CheckConstraint(
            "(state = 'revoked' AND revoked_at IS NOT NULL "
            "AND revoked_by_reference IS NOT NULL) "
            "OR (state != 'revoked' AND revoked_at IS NULL "
            "AND revoked_by_user_id IS NULL "
            "AND revoked_by_reference IS NULL)",
            name="ck_policy_authority_grants_revocation_integrity",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    policy_key = Column(
        String(128),
        nullable=False,
    )

    policy_version = Column(
        String(32),
        nullable=False,
    )

    skill_key = Column(
        String(128),
        nullable=False,
    )

    scope_type = Column(
        String(32),
        nullable=False,
    )

    state = Column(
        String(16),
        nullable=False,
        default="active",
        server_default="active",
    )

    granted_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name=(
                "fk_policy_authority_grants_"
                "granted_by_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    granted_by_reference = Column(
        String(255),
        nullable=False,
    )

    granted_by_role = Column(
        String(32),
        nullable=False,
    )

    valid_from = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    revoked_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    revoked_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name=(
                "fk_policy_authority_grants_"
                "revoked_by_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    revoked_by_reference = Column(
        String(255),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_policy_authority_grants_state_expires",
    PolicyAuthorityGrant.state,
    PolicyAuthorityGrant.expires_at,
    PolicyAuthorityGrant.id,
)

Index(
    "ux_policy_authority_grants_active_policy_skill",
    PolicyAuthorityGrant.policy_key,
    PolicyAuthorityGrant.skill_key,
    unique=True,
    postgresql_where=text("state = 'active'"),
)
