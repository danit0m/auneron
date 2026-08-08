"""add user authentication

Revision ID: 8b0c5f3e2a19
Revises: 057e1ffeec3c
Create Date: 2026-08-07
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "8b0c5f3e2a19"
down_revision: str | None = "057e1ffeec3c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "email",
            sa.String(length=254),
            nullable=False,
        ),
        sa.Column(
            "password_hash",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.String(length=32),
            server_default="viewer",
            nullable=False,
        ),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) >= 2",
            name="ck_users_name_min_length",
        ),
        sa.CheckConstraint(
            "char_length(btrim(email)) >= 3",
            name="ck_users_email_min_length",
        ),
        sa.CheckConstraint(
            "email = lower(email)",
            name="ck_users_email_lowercase",
        ),
        sa.CheckConstraint(
            "role IN ("
            "'viewer', "
            "'analyst', "
            "'manager', "
            "'executive', "
            "'administrator', "
            "'developer'"
            ")",
            name="ck_users_role_valid",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "email",
            name="uq_users_email",
        ),
    )

    op.create_table(
        "auth_sessions",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "token_hash",
            sa.String(length=64),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "elevated_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_auth_sessions_expiration",
        ),
        sa.CheckConstraint(
            "elevated_until IS NULL "
            "OR elevated_until <= expires_at",
            name=(
                "ck_auth_sessions_elevation_expiration"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_auth_sessions_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "token_hash",
            name="uq_auth_sessions_token_hash",
        ),
    )

    op.create_index(
        "ix_auth_sessions_user_id",
        "auth_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_auth_sessions_expires_at",
        "auth_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_sessions_revoked_at",
        "auth_sessions",
        ["revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auth_sessions_revoked_at",
        table_name="auth_sessions",
    )
    op.drop_index(
        "ix_auth_sessions_expires_at",
        table_name="auth_sessions",
    )
    op.drop_index(
        "ix_auth_sessions_user_id",
        table_name="auth_sessions",
    )
    op.drop_table("auth_sessions")
    op.drop_table("users")
