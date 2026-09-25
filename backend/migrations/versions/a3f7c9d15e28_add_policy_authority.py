"""add policy authority

Revision ID: a3f7c9d15e28
Revises: 16674f0a7aa3
Create Date: 2026-09-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f7c9d15e28'
down_revision: Union[str, Sequence[str], None] = '16674f0a7aa3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "policy_authority_grants",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("policy_key", sa.String(length=128), nullable=False),
        sa.Column(
            "policy_version", sa.String(length=32), nullable=False
        ),
        sa.Column("skill_key", sa.String(length=128), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "granted_by_user_id", sa.Integer(), nullable=True
        ),
        sa.Column(
            "granted_by_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "granted_by_role", sa.String(length=32), nullable=False
        ),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revoked_by_user_id", sa.Integer(), nullable=True
        ),
        sa.Column(
            "revoked_by_reference",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(policy_key)) >= 1",
            name="ck_policy_authority_grants_policy_key_not_blank",
        ),
        sa.CheckConstraint(
            "char_length(btrim(policy_version)) >= 1",
            name="ck_policy_authority_grants_policy_version_not_blank",
        ),
        sa.CheckConstraint(
            "skill_key IN ('account.mark_overdue')",
            name="ck_policy_authority_grants_skill_key_valid",
        ),
        sa.CheckConstraint(
            "scope_type IN ('deployment_wide')",
            name="ck_policy_authority_grants_scope_type_valid",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'revoked', 'expired')",
            name="ck_policy_authority_grants_state_valid",
        ),
        sa.CheckConstraint(
            "char_length(btrim(granted_by_reference)) >= 1",
            name="ck_policy_authority_grants_granted_by_ref_not_blank",
        ),
        sa.CheckConstraint(
            "granted_by_role IN ("
            "'viewer', 'analyst', 'manager', 'executive', "
            "'administrator', 'developer'"
            ")",
            name="ck_policy_authority_grants_granted_by_role_valid",
        ),
        sa.CheckConstraint(
            "expires_at > valid_from",
            name="ck_policy_authority_grants_expiration_order",
        ),
        sa.CheckConstraint(
            "(state = 'revoked' AND revoked_at IS NOT NULL "
            "AND revoked_by_reference IS NOT NULL) "
            "OR (state != 'revoked' AND revoked_at IS NULL "
            "AND revoked_by_user_id IS NULL "
            "AND revoked_by_reference IS NULL)",
            name="ck_policy_authority_grants_revocation_integrity",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["users.id"],
            name=(
                "fk_policy_authority_grants_"
                "granted_by_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name=(
                "fk_policy_authority_grants_"
                "revoked_by_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_policy_authority_grants_state_expires",
        "policy_authority_grants",
        ["state", "expires_at", "id"],
        unique=False,
    )
    op.create_index(
        "ux_policy_authority_grants_active_policy_skill",
        "policy_authority_grants",
        ["policy_key", "skill_key"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
    )

    op.create_table(
        "policy_authority_consumptions",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column(
            "policy_authority_grant_id",
            sa.BigInteger(),
            nullable=False,
        ),
        sa.Column(
            "skill_invocation_id", sa.BigInteger(), nullable=False
        ),
        sa.Column(
            "episode_scope_key",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "target_account_id", sa.Integer(), nullable=True
        ),
        sa.Column(
            "policy_version_applied",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column(
            "evidence_digest", sa.String(length=64), nullable=False
        ),
        sa.Column(
            "input_digest", sa.String(length=64), nullable=False
        ),
        sa.Column(
            "consumer_actor_type",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "consumer_reference",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "consumed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(episode_scope_key)) >= 1",
            name=(
                "ck_policy_authority_consumptions_"
                "episode_key_not_blank"
            ),
        ),
        sa.CheckConstraint(
            "char_length(btrim(policy_version_applied)) >= 1",
            name=(
                "ck_policy_authority_consumptions_"
                "version_not_blank"
            ),
        ),
        sa.CheckConstraint(
            "char_length(evidence_digest) = 64 "
            "AND evidence_digest = lower(evidence_digest)",
            name=(
                "ck_policy_authority_consumptions_"
                "evidence_digest_format"
            ),
        ),
        sa.CheckConstraint(
            "char_length(input_digest) = 64 "
            "AND input_digest = lower(input_digest)",
            name=(
                "ck_policy_authority_consumptions_"
                "input_digest_format"
            ),
        ),
        sa.CheckConstraint(
            "consumer_actor_type IN "
            "('agent', 'system', 'integration')",
            name=(
                "ck_policy_authority_consumptions_"
                "actor_type_valid"
            ),
        ),
        sa.CheckConstraint(
            "char_length(btrim(consumer_reference)) >= 1",
            name=(
                "ck_policy_authority_consumptions_"
                "consumer_ref_not_blank"
            ),
        ),
        sa.CheckConstraint(
            "target_account_id IS NULL OR target_account_id > 0",
            name=(
                "ck_policy_authority_consumptions_"
                "account_id_positive"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["policy_authority_grant_id"],
            ["policy_authority_grants.id"],
            name="fk_pac_grant_id_policy_authority_grants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["skill_invocation_id"],
            ["skill_invocations.id"],
            name="fk_pac_invocation_id_skill_invocations",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "episode_scope_key",
            name="uq_policy_authority_consumptions_episode",
        ),
        sa.UniqueConstraint(
            "skill_invocation_id",
            name="uq_policy_authority_consumptions_invocation",
        ),
    )
    op.create_index(
        "ix_policy_authority_consumptions_grant_consumed",
        "policy_authority_consumptions",
        ["policy_authority_grant_id", "consumed_at", "id"],
        unique=False,
    )

    # BEV: torna approval_consumption_id opcional e adiciona a fonte
    # alternativa policy-governed, mutuamente exclusiva.
    op.alter_column(
        "business_effect_verifications",
        "approval_consumption_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.add_column(
        "business_effect_verifications",
        sa.Column(
            "policy_authority_consumption_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_bev_policy_authority_consumption_id_pac",
        "business_effect_verifications",
        "policy_authority_consumptions",
        ["policy_authority_consumption_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_bev_consumption_source_xor",
        "business_effect_verifications",
        "(approval_consumption_id IS NOT NULL "
        "AND policy_authority_consumption_id IS NULL) "
        "OR (approval_consumption_id IS NULL "
        "AND policy_authority_consumption_id IS NOT NULL)",
    )
    op.create_unique_constraint(
        "uq_bev_policy_consumption",
        "business_effect_verifications",
        ["policy_authority_consumption_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_bev_policy_consumption",
        "business_effect_verifications",
        type_="unique",
    )
    op.drop_constraint(
        "ck_bev_consumption_source_xor",
        "business_effect_verifications",
        type_="check",
    )
    op.drop_constraint(
        "fk_bev_policy_authority_consumption_id_pac",
        "business_effect_verifications",
        type_="foreignkey",
    )
    op.drop_column(
        "business_effect_verifications",
        "policy_authority_consumption_id",
    )
    op.alter_column(
        "business_effect_verifications",
        "approval_consumption_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    op.drop_index(
        "ix_policy_authority_consumptions_grant_consumed",
        table_name="policy_authority_consumptions",
    )
    op.drop_table("policy_authority_consumptions")

    op.drop_index(
        "ux_policy_authority_grants_active_policy_skill",
        table_name="policy_authority_grants",
    )
    op.drop_index(
        "ix_policy_authority_grants_state_expires",
        table_name="policy_authority_grants",
    )
    op.drop_table("policy_authority_grants")
