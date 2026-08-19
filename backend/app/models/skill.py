from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import func
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB

from app.database.database import Base


SkillJSON = JSON().with_variant(
    JSONB(),
    "postgresql",
)


SkillNullableJSON = JSON(
    none_as_null=True
).with_variant(
    JSONB(
        none_as_null=True
    ),
    "postgresql",
)


class SkillDefinition(Base):
    __tablename__ = "skills"

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(skill_key)) >= 1",
            name="ck_skills_skill_key_not_blank",
        ),
        CheckConstraint(
            "skill_key = lower(btrim(skill_key))",
            name="ck_skills_skill_key_canonical",
        ),
        CheckConstraint(
            "char_length(btrim(provider)) >= 1",
            name="ck_skills_provider_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(display_name)) >= 1",
            name="ck_skills_display_name_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(description)) >= 1",
            name="ck_skills_description_not_blank",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'retired')",
            name="ck_skills_status_valid",
        ),
        UniqueConstraint(
            "skill_key",
            name="uq_skills_skill_key",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    skill_key = Column(
        String(128),
        nullable=False,
    )

    provider = Column(
        String(128),
        nullable=False,
    )

    display_name = Column(
        String(160),
        nullable=False,
    )

    description = Column(
        Text,
        nullable=False,
    )

    status = Column(
        String(20),
        nullable=False,
        default="active",
        server_default="active",
    )

    created_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_skills_created_by_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index(
    "ix_skills_status_key",
    SkillDefinition.status,
    SkillDefinition.skill_key,
)


class SkillVersion(Base):
    __tablename__ = "skill_versions"

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(version)) >= 1",
            name="ck_skill_versions_version_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(handler_reference)) >= 1",
            name="ck_skill_versions_handler_not_blank",
        ),
        CheckConstraint(
            "runtime_kind IN ('internal_python', 'plugin')",
            name="ck_skill_versions_runtime_kind_valid",
        ),
        CheckConstraint(
            "execution_mode IN ('read_only', 'mutating', 'external')",
            name="ck_skill_versions_execution_mode_valid",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')",
            name="ck_skill_versions_status_valid",
        ),
        CheckConstraint(
            "char_length(manifest_digest) = 64 "
            "AND manifest_digest = lower(manifest_digest)",
            name="ck_skill_versions_manifest_digest_format",
        ),
        CheckConstraint(
            "timeout_seconds >= 1 AND timeout_seconds <= 300",
            name="ck_skill_versions_timeout_range",
        ),
        CheckConstraint(
            "max_output_bytes >= 1024 "
            "AND max_output_bytes <= 1048576",
            name="ck_skill_versions_max_output_range",
        ),
        CheckConstraint(
            "(status = 'draft' "
            "AND published_at IS NULL "
            "AND retired_at IS NULL) OR "
            "(status = 'published' "
            "AND published_at IS NOT NULL "
            "AND retired_at IS NULL) OR "
            "(status = 'retired' "
            "AND published_at IS NOT NULL "
            "AND retired_at IS NOT NULL "
            "AND retired_at >= published_at)",
            name="ck_skill_versions_status_timestamps",
        ),
        UniqueConstraint(
            "skill_id",
            "version",
            name="uq_skill_versions_skill_version",
        ),
        UniqueConstraint(
            "skill_id",
            "manifest_digest",
            name="uq_skill_versions_skill_digest",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    skill_id = Column(
        BigInteger,
        ForeignKey(
            "skills.id",
            name="fk_skill_versions_skill_id_skills",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    version = Column(
        String(32),
        nullable=False,
    )

    runtime_kind = Column(
        String(24),
        nullable=False,
    )

    handler_reference = Column(
        String(320),
        nullable=False,
    )

    execution_mode = Column(
        String(20),
        nullable=False,
    )

    manifest_digest = Column(
        String(64),
        nullable=False,
    )

    manifest = Column(
        SkillJSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    input_schema = Column(
        SkillJSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    output_schema = Column(
        SkillJSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    timeout_seconds = Column(
        Integer,
        nullable=False,
        default=30,
        server_default="30",
    )

    max_output_bytes = Column(
        Integer,
        nullable=False,
        default=65536,
        server_default="65536",
    )

    status = Column(
        String(20),
        nullable=False,
        default="draft",
        server_default="draft",
    )

    published_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    retired_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name=(
                "fk_skill_versions_created_by_user_id_users"
            ),
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_skill_versions_skill_status_created",
    SkillVersion.skill_id,
    SkillVersion.status,
    SkillVersion.created_at,
    SkillVersion.id,
)

Index(
    "ix_skill_versions_status_runtime",
    SkillVersion.status,
    SkillVersion.runtime_kind,
)


class SkillCapability(Base):
    __tablename__ = "skill_capabilities"

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(capability_key)) >= 1",
            name="ck_skill_capabilities_key_not_blank",
        ),
        CheckConstraint(
            "capability_key = lower(btrim(capability_key))",
            name="ck_skill_capabilities_key_canonical",
        ),
        CheckConstraint(
            "access_mode IN ('read', 'write', 'execute')",
            name="ck_skill_capabilities_access_mode_valid",
        ),
        CheckConstraint(
            "resource_scope IN ("
            "'internal', 'account', 'user', 'external'"
            ")",
            name="ck_skill_capabilities_resource_scope_valid",
        ),
        UniqueConstraint(
            "skill_version_id",
            "capability_key",
            "access_mode",
            "resource_scope",
            name="uq_skill_capabilities_declaration",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    skill_version_id = Column(
        BigInteger,
        ForeignKey(
            "skill_versions.id",
            name=(
                "fk_skill_capabilities_version_id_"
                "skill_versions"
            ),
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    capability_key = Column(
        String(160),
        nullable=False,
    )

    access_mode = Column(
        String(16),
        nullable=False,
    )

    resource_scope = Column(
        String(20),
        nullable=False,
    )

    required = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index(
    "ix_skill_capabilities_key_mode",
    SkillCapability.capability_key,
    SkillCapability.access_mode,
    SkillCapability.resource_scope,
)

Index(
    "ix_skill_capabilities_version_required",
    SkillCapability.skill_version_id,
    SkillCapability.required,
)


class AgentSkillBinding(Base):
    __tablename__ = "agent_skill_bindings"

    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(agent_name)) >= 1",
            name="ck_agent_skill_bindings_agent_name_not_blank",
        ),
        CheckConstraint(
            "priority >= 1 AND priority <= 1000",
            name="ck_agent_skill_bindings_priority_range",
        ),
        UniqueConstraint(
            "agent_name",
            "skill_version_id",
            name="uq_agent_skill_bindings_agent_version",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    agent_name = Column(
        String(160),
        nullable=False,
    )

    skill_version_id = Column(
        BigInteger,
        ForeignKey(
            "skill_versions.id",
            name=(
                "fk_agent_skill_bindings_version_id_"
                "skill_versions"
            ),
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    priority = Column(
        Integer,
        nullable=False,
        default=100,
        server_default="100",
    )

    enabled = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    configuration = Column(
        SkillJSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    created_by_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name=(
                "fk_agent_skill_bindings_created_by_user_id_"
                "users"
            ),
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


Index(
    "ix_agent_skill_bindings_agent_enabled_priority",
    AgentSkillBinding.agent_name,
    AgentSkillBinding.enabled,
    AgentSkillBinding.priority,
    AgentSkillBinding.id,
)

Index(
    "ix_agent_skill_bindings_version_enabled",
    AgentSkillBinding.skill_version_id,
    AgentSkillBinding.enabled,
)

class SkillInvocation(Base):
    __tablename__ = "skill_invocations"

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'agent', 'system', 'integration')",
            name="ck_skill_invocations_actor_type_valid",
        ),
        CheckConstraint(
            "char_length(btrim(actor_reference)) >= 1",
            name="ck_skill_invocations_actor_reference_not_blank",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR ("
            "char_length(btrim(idempotency_key)) >= 1 "
            "AND idempotency_key = lower(btrim(idempotency_key))"
            ")",
            name="ck_skill_invocations_idempotency_key_canonical",
        ),
        CheckConstraint(
            "char_length(request_fingerprint) = 64 "
            "AND request_fingerprint = lower(request_fingerprint)",
            name="ck_skill_invocations_request_fingerprint_format",
        ),
        CheckConstraint(
            "char_length(input_digest) = 64 "
            "AND input_digest = lower(input_digest)",
            name="ck_skill_invocations_input_digest_format",
        ),
        CheckConstraint(
            "output_digest IS NULL OR ("
            "char_length(output_digest) = 64 "
            "AND output_digest = lower(output_digest)"
            ")",
            name="ck_skill_invocations_output_digest_format",
        ),
        CheckConstraint(
            "status IN ("
            "'running', 'succeeded', 'failed', 'timed_out', 'rejected'"
            ")",
            name="ck_skill_invocations_status_valid",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_skill_invocations_duration_nonnegative",
        ),
        CheckConstraint(
            "output_bytes IS NULL OR ("
            "output_bytes >= 0 AND output_bytes <= 1048576"
            ")",
            name="ck_skill_invocations_output_bytes_range",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_skill_invocations_finished_after_started",
        ),
        CheckConstraint(
            "("
            "status = 'running' "
            "AND finished_at IS NULL "
            "AND duration_ms IS NULL "
            "AND output_payload IS NULL "
            "AND output_digest IS NULL "
            "AND output_bytes IS NULL "
            "AND error_code IS NULL"
            ") OR ("
            "status = 'succeeded' "
            "AND finished_at IS NOT NULL "
            "AND duration_ms IS NOT NULL "
            "AND output_payload IS NOT NULL "
            "AND output_digest IS NOT NULL "
            "AND output_bytes IS NOT NULL "
            "AND error_code IS NULL"
            ") OR ("
            "status IN ('failed', 'timed_out', 'rejected') "
            "AND finished_at IS NOT NULL "
            "AND duration_ms IS NOT NULL "
            "AND output_payload IS NULL "
            "AND output_digest IS NULL "
            "AND output_bytes IS NULL "
            "AND error_code IS NOT NULL "
            "AND char_length(btrim(error_code)) >= 1"
            ")",
            name="ck_skill_invocations_terminal_integrity",
        ),
        UniqueConstraint(
            "skill_version_id",
            "actor_type",
            "actor_reference",
            "idempotency_key",
            name="uq_skill_invocations_idempotency_scope",
        ),
    )

    id = Column(
        BigInteger,
        primary_key=True,
    )

    skill_version_id = Column(
        BigInteger,
        ForeignKey(
            "skill_versions.id",
            name="fk_skill_invocations_version_id_skill_versions",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    actor_type = Column(
        String(24),
        nullable=False,
    )

    actor_reference = Column(
        String(255),
        nullable=False,
    )

    actor_user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            name="fk_skill_invocations_actor_user_id_users",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    idempotency_key = Column(
        String(255),
        nullable=True,
    )

    request_fingerprint = Column(
        String(64),
        nullable=False,
    )

    input_digest = Column(
        String(64),
        nullable=False,
    )

    status = Column(
        String(20),
        nullable=False,
        default="running",
        server_default="running",
    )

    output_payload = Column(
        SkillNullableJSON,
        nullable=True,
    )

    output_digest = Column(
        String(64),
        nullable=True,
    )

    output_bytes = Column(
        Integer,
        nullable=True,
    )

    error_code = Column(
        String(64),
        nullable=True,
    )

    duration_ms = Column(
        Integer,
        nullable=True,
    )

    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    finished_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )


Index(
    "ix_skill_invocations_version_started",
    SkillInvocation.skill_version_id,
    SkillInvocation.started_at,
    SkillInvocation.id,
)

Index(
    "ix_skill_invocations_actor_started",
    SkillInvocation.actor_type,
    SkillInvocation.actor_reference,
    SkillInvocation.started_at,
)

Index(
    "ix_skill_invocations_status_started",
    SkillInvocation.status,
    SkillInvocation.started_at,
    SkillInvocation.id,
)
