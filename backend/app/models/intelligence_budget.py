from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import UniqueConstraint
from sqlalchemy import func
from sqlalchemy import text

from app.database.database import Base


class IntelligenceBudget(Base):
    """
    INTELLIGENCE-IMPL-0 -- saldo de orcamento do gateway multi-modelo.

    Baldes por orcamento (INTELLIGENCE-02A): L limite, R reservado
    (pior caso de chamadas reserved + in_flight), S liquidado
    (min(real, reservado)), U desconhecido (retido no pior caso) e
    O excedente confirmado (max(real - reservado, 0)).

    BI-1 (CHECK): R + S + U <= L, todos >= 0 (dinheiro e tokens).
    BI-2 (predicado de admissao, no UPDATE...WHERE do repositorio):
    R + S + U + O + r <= L.

    Esta tabela guarda apenas o SALDO CORRENTE. Nao ha historico
    imutavel de transicoes (intelligence_budget_entries e posterior);
    ver docs/intelligence/BUDGET_LEDGER.md.
    """

    __tablename__ = "intelligence_budgets"

    __table_args__ = (
        CheckConstraint(
            "scope_kind IN ("
            "'user_period', 'objective', 'deployment_period'"
            ")",
            name="ck_intelligence_budgets_scope_kind_valid",
        ),
        CheckConstraint(
            "char_length(btrim(scope_ref)) >= 1",
            name="ck_intelligence_budgets_scope_ref_not_blank",
        ),
        CheckConstraint(
            "(scope_kind = 'objective' "
            "AND period_start IS NULL AND period_end IS NULL) "
            "OR (scope_kind != 'objective' "
            "AND period_start IS NOT NULL "
            "AND period_end IS NOT NULL "
            "AND period_end > period_start)",
            name="ck_intelligence_budgets_period_integrity",
        ),
        CheckConstraint(
            "limit_micro >= 0 AND limit_tokens >= 0 "
            "AND reserved_micro >= 0 AND reserved_tokens >= 0 "
            "AND settled_micro >= 0 AND settled_tokens >= 0 "
            "AND unknown_micro >= 0 AND unknown_tokens >= 0 "
            "AND overrun_micro >= 0 AND overrun_tokens >= 0",
            name="ck_intelligence_budgets_non_negative",
        ),
        CheckConstraint(
            "reserved_micro + settled_micro + unknown_micro "
            "<= limit_micro",
            name="ck_intelligence_budgets_micro_within_limit",
        ),
        CheckConstraint(
            "reserved_tokens + settled_tokens + unknown_tokens "
            "<= limit_tokens",
            name="ck_intelligence_budgets_tokens_within_limit",
        ),
        UniqueConstraint(
            "scope_kind",
            "scope_ref",
            "period_start",
            name="uq_intelligence_budgets_scope_period",
        ),
        Index(
            "uq_intelligence_budgets_scope_no_period",
            "scope_kind",
            "scope_ref",
            unique=True,
            postgresql_where=text("period_start IS NULL"),
        ),
    )

    id = Column(BigInteger, primary_key=True)

    scope_kind = Column(String(24), nullable=False)
    scope_ref = Column(String(128), nullable=False)
    period_start = Column(DateTime(timezone=True), nullable=True)
    period_end = Column(DateTime(timezone=True), nullable=True)

    limit_micro = Column(BigInteger, nullable=False)
    limit_tokens = Column(BigInteger, nullable=False)

    reserved_micro = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    reserved_tokens = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    settled_micro = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    settled_tokens = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    unknown_micro = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    unknown_tokens = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    overrun_micro = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    overrun_tokens = Column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    version = Column(
        Integer, nullable=False, default=0, server_default="0"
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
    )


class IntelligenceModelCall(Base):
    """
    INTELLIGENCE-IMPL-0 -- uma tentativa de chamada de modelo.

    Cada linha esta em exatamente um estado e contribui para
    exatamente um balde de cada orcamento associado (BI-3). Cada
    tentativa (retry/fallback) e uma linha propria com nova reserva;
    attempt > 1 aponta para a primeira tentativa em parent_call_id.

    Somente digests (SHA-256) do pedido/resposta sao persistidos;
    nunca o conteudo. data_classification e restrito a 'synthetic'
    por CHECK (dados reais exigem migracao futura aprovada). provider
    e restrito a 'fake' por CHECK (adaptadores reais exigem migracao
    futura aprovada).

    objective_id/plan_id nao tem FK: as tabelas alvo ainda nao
    existem.
    """

    __tablename__ = "intelligence_model_calls"

    __table_args__ = (
        CheckConstraint(
            "state IN ("
            "'reserved', 'in_flight', 'settled', 'unknown', "
            "'released'"
            ")",
            name="ck_intelligence_model_calls_state_valid",
        ),
        CheckConstraint(
            "data_classification = 'synthetic'",
            name="ck_intelligence_model_calls_synthetic_only",
        ),
        CheckConstraint(
            "provider IN ('fake')",
            name="ck_intelligence_model_calls_provider_valid",
        ),
        CheckConstraint(
            "char_length(btrim(call_key)) >= 1",
            name="ck_intelligence_model_calls_call_key_not_blank",
        ),
        CheckConstraint(
            "attempt >= 1 AND ((attempt = 1 "
            "AND parent_call_id IS NULL) "
            "OR (attempt > 1 AND parent_call_id IS NOT NULL))",
            name="ck_intelligence_model_calls_attempt_parent",
        ),
        CheckConstraint(
            "user_budget_id IS NOT NULL "
            "OR objective_budget_id IS NOT NULL "
            "OR deployment_budget_id IS NOT NULL",
            name="ck_intelligence_model_calls_has_budget",
        ),
        CheckConstraint(
            "reserved_micro > 0 AND reserved_tokens > 0 "
            "AND (actual_micro IS NULL OR actual_micro >= 0) "
            "AND (actual_tokens IS NULL OR actual_tokens >= 0)",
            name="ck_intelligence_model_calls_amounts_valid",
        ),
        CheckConstraint(
            "release_basis IS NULL OR release_basis IN ("
            "'never_sent', 'provider_documented', "
            "'admin_reconciliation'"
            ")",
            name="ck_intelligence_model_calls_release_basis_valid",
        ),
        CheckConstraint(
            "(request_digest IS NULL "
            "OR request_digest ~ '^[0-9a-f]{64}$') "
            "AND (response_digest IS NULL "
            "OR response_digest ~ '^[0-9a-f]{64}$')",
            name="ck_intelligence_model_calls_digest_format",
        ),
        CheckConstraint(
            "(state = 'reserved' AND sent_at IS NULL "
            "AND unknown_at IS NULL AND settled_at IS NULL "
            "AND released_at IS NULL AND actual_micro IS NULL "
            "AND actual_tokens IS NULL "
            "AND release_basis IS NULL) "
            "OR (state = 'in_flight' AND sent_at IS NOT NULL "
            "AND unknown_at IS NULL AND settled_at IS NULL "
            "AND released_at IS NULL AND actual_micro IS NULL "
            "AND actual_tokens IS NULL "
            "AND release_basis IS NULL) "
            "OR (state = 'unknown' AND sent_at IS NOT NULL "
            "AND unknown_at IS NOT NULL AND settled_at IS NULL "
            "AND released_at IS NULL AND actual_micro IS NULL "
            "AND actual_tokens IS NULL "
            "AND release_basis IS NULL) "
            "OR (state = 'settled' AND sent_at IS NOT NULL "
            "AND settled_at IS NOT NULL AND released_at IS NULL "
            "AND actual_micro IS NOT NULL "
            "AND actual_tokens IS NOT NULL "
            "AND release_basis IS NULL) "
            "OR (state = 'released' AND released_at IS NOT NULL "
            "AND release_basis IS NOT NULL "
            "AND settled_at IS NULL AND actual_micro IS NULL "
            "AND actual_tokens IS NULL)",
            name="ck_intelligence_model_calls_state_integrity",
        ),
        CheckConstraint(
            "(release_basis IS DISTINCT FROM 'provider_documented' "
            "OR evidence_ref IS NOT NULL) "
            "AND (release_basis IS DISTINCT FROM "
            "'admin_reconciliation' "
            "OR (evidence_ref IS NOT NULL "
            "AND reconciled_by_reference IS NOT NULL "
            "AND reconciled_at IS NOT NULL)) "
            "AND (release_basis IS DISTINCT FROM 'never_sent' "
            "OR sent_at IS NULL OR evidence_ref IS NOT NULL) "
            "AND ((reconciled_at IS NULL) = "
            "(reconciled_by_reference IS NULL)) "
            "AND (reconciled_at IS NULL "
            "OR evidence_ref IS NOT NULL)",
            name="ck_intelligence_model_calls_evidence_integrity",
        ),
        UniqueConstraint(
            "call_key",
            name="uq_intelligence_model_calls_call_key",
        ),
        Index(
            "ix_intelligence_model_calls_state_deadline",
            "state",
            "deadline_at",
        ),
    )

    id = Column(BigInteger, primary_key=True)

    call_key = Column(String(160), nullable=False)
    purpose = Column(String(64), nullable=False)

    objective_id = Column(BigInteger, nullable=True)
    plan_id = Column(BigInteger, nullable=True)

    parent_call_id = Column(
        BigInteger,
        ForeignKey(
            "intelligence_model_calls.id",
            name=(
                "fk_intelligence_calls_parent_call_id"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    attempt = Column(
        Integer, nullable=False, default=1, server_default="1"
    )

    provider = Column(String(32), nullable=False)
    model_profile = Column(String(64), nullable=False)
    model_id = Column(String(128), nullable=False)
    prompt_template_id = Column(String(64), nullable=True)
    output_schema_id = Column(String(64), nullable=True)

    data_classification = Column(
        String(16),
        nullable=False,
        default="synthetic",
        server_default="synthetic",
    )

    state = Column(
        String(16),
        nullable=False,
        default="reserved",
        server_default="reserved",
    )

    user_budget_id = Column(
        BigInteger,
        ForeignKey(
            "intelligence_budgets.id",
            name=(
                "fk_intelligence_calls_user_budget_id"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    objective_budget_id = Column(
        BigInteger,
        ForeignKey(
            "intelligence_budgets.id",
            name=(
                "fk_intelligence_calls_objective_budget_id"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    deployment_budget_id = Column(
        BigInteger,
        ForeignKey(
            "intelligence_budgets.id",
            name=(
                "fk_intelligence_calls_deployment_budget_id"
            ),
            ondelete="RESTRICT",
        ),
        nullable=True,
    )

    reserved_micro = Column(BigInteger, nullable=False)
    reserved_tokens = Column(BigInteger, nullable=False)
    actual_micro = Column(BigInteger, nullable=True)
    actual_tokens = Column(BigInteger, nullable=True)

    release_basis = Column(String(24), nullable=True)
    evidence_ref = Column(String(256), nullable=True)
    reconciled_by_reference = Column(String(128), nullable=True)

    error_class = Column(String(64), nullable=True)
    request_digest = Column(String(64), nullable=True)
    response_digest = Column(String(64), nullable=True)

    reserved_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    sent_at = Column(DateTime(timezone=True), nullable=True)
    unknown_at = Column(DateTime(timezone=True), nullable=True)
    settled_at = Column(DateTime(timezone=True), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    reconciled_at = Column(DateTime(timezone=True), nullable=True)
    deadline_at = Column(DateTime(timezone=True), nullable=False)
