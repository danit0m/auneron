from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Final

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.intelligence.gateway.contracts import CallState
from app.intelligence.gateway.contracts import DataClassification
from app.intelligence.gateway.contracts import ReleaseBasis
from app.intelligence.gateway.errors import GatewayError
from app.intelligence.gateway.errors import GatewayErrorCode
from app.intelligence.gateway.pricing import Reservation
from app.models.intelligence_budget import IntelligenceBudget
from app.models.intelligence_budget import IntelligenceModelCall
from app.repositories.intelligence_budget_repository import (
    BPS_DENOMINATOR,
)
from app.repositories.intelligence_budget_repository import (
    IntelligenceBudgetRepository,
)


# T6 table: (provider, error_class) -> documentation reference proving
# that the provider does NOT charge for that failure. EMPTY BY DEFAULT:
# until the PO approves a documented entry, no in_flight call can be
# released by policy -- 400/429/5xx/timeouts stay `unknown` (BI-5).
PROVIDER_NO_CHARGE_POLICIES: Final[dict[tuple[str, str], str]] = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class BudgetSnapshot:
    id: int
    limit_micro: int
    limit_tokens: int
    reserved_micro: int
    reserved_tokens: int
    settled_micro: int
    settled_tokens: int
    unknown_micro: int
    unknown_tokens: int
    overrun_micro: int
    overrun_tokens: int
    version: int

    @property
    def confirmed_micro(self) -> int:
        """BI-4: confirmed spend is always S + O."""
        return self.settled_micro + self.overrun_micro


@dataclass(frozen=True)
class ConservationReport:
    """
    "Current accounting conservation" (BI-3): the buckets recomputed
    from the call rows must equal the stored buckets. This verifies
    the CURRENT state only; it is not an immutable transition history.
    """

    budget_id: int
    consistent: bool
    stored: dict[str, int]
    recomputed: dict[str, int]


@dataclass(frozen=True)
class RecoveryReport:
    released_never_sent: tuple[int, ...]
    marked_unknown: tuple[int, ...]


def _snapshot(budget: IntelligenceBudget) -> BudgetSnapshot:
    return BudgetSnapshot(
        id=budget.id,
        limit_micro=budget.limit_micro,
        limit_tokens=budget.limit_tokens,
        reserved_micro=budget.reserved_micro,
        reserved_tokens=budget.reserved_tokens,
        settled_micro=budget.settled_micro,
        settled_tokens=budget.settled_tokens,
        unknown_micro=budget.unknown_micro,
        unknown_tokens=budget.unknown_tokens,
        overrun_micro=budget.overrun_micro,
        overrun_tokens=budget.overrun_tokens,
        version=budget.version,
    )


def _split(actual: int, reserved: int) -> tuple[int, int]:
    """(allocated, overrun) = (min(a, r), max(a - r, 0))."""
    return min(actual, reserved), max(actual - reserved, 0)


class BudgetLedger:
    """
    Transactional budget ledger (INTELLIGENCE-02A transitions T1-T8).

    Every public transition runs in its OWN short database transaction
    (own Session from the factory) and commits before returning, so:
      * no transaction is ever held open during a provider call;
      * the write-ahead `in_flight` mark (T2) is durable BEFORE the
        gateway writes to the network;
      * a process death at any point leaves a state that recover()
        resolves conservatively (never a silent release of a call that
        may have been sent).

    Rejected transitions raise GatewayError deterministically; the
    ledger never "repairs" by guessing.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        unknown_fraction_bps: int,
        recovery_grace_seconds: int,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        if not 1 <= unknown_fraction_bps <= BPS_DENOMINATOR:
            raise ValueError("unknown_fraction_bps out of range.")
        if recovery_grace_seconds < 0:
            raise ValueError("recovery_grace_seconds must be >= 0.")

        self._session_factory = session_factory
        self._theta_bps = unknown_fraction_bps
        self._grace = timedelta(seconds=recovery_grace_seconds)
        self._clock = clock

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------

    @contextmanager
    def _tx(
        self,
    ) -> Iterator[tuple[Session, IntelligenceBudgetRepository]]:
        db = self._session_factory()

        try:
            yield db, IntelligenceBudgetRepository(db)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _budget_ids(call: IntelligenceModelCall) -> list[int]:
        return sorted(
            {
                budget_id
                for budget_id in (
                    call.user_budget_id,
                    call.objective_budget_id,
                    call.deployment_budget_id,
                )
                if budget_id is not None
            }
        )

    @staticmethod
    def _locked_call(
        repo: IntelligenceBudgetRepository,
        call_id: int,
    ) -> IntelligenceModelCall:
        call = repo.get_call_for_update(call_id)

        if call is None:
            raise GatewayError(
                GatewayErrorCode.CALL_NOT_FOUND,
                "Model call not found.",
            )

        return call

    @staticmethod
    def _reject(call: IntelligenceModelCall, expected: str) -> None:
        raise GatewayError(
            GatewayErrorCode.INVALID_TRANSITION,
            f"Call is {call.state}, transition requires {expected}.",
        )

    def _apply(
        self,
        repo: IntelligenceBudgetRepository,
        call: IntelligenceModelCall,
        now: datetime,
        **deltas: int,
    ) -> None:
        try:
            for budget_id in self._budget_ids(call):
                repo.apply_deltas(budget_id, now=now, **deltas)

            repo.db.flush()
        except IntegrityError as error:
            raise GatewayError(
                GatewayErrorCode.INVALID_TRANSITION,
                "Ledger invariant rejected the transition.",
            ) from error

    # ------------------------------------------------------------------
    # budgets (setup / read)
    # ------------------------------------------------------------------

    def create_budget(
        self,
        *,
        scope_kind: str,
        scope_ref: str,
        limit_micro: int,
        limit_tokens: int,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> int:
        with self._tx() as (_, repo):
            budget = repo.add_budget(
                IntelligenceBudget(
                    scope_kind=scope_kind,
                    scope_ref=scope_ref,
                    period_start=period_start,
                    period_end=period_end,
                    limit_micro=limit_micro,
                    limit_tokens=limit_tokens,
                    updated_at=self._clock(),
                )
            )
            return budget.id

    def snapshot(self, budget_id: int) -> BudgetSnapshot:
        with self._tx() as (_, repo):
            budget = repo.read_budget_fresh(budget_id)

            if budget is None:
                raise GatewayError(
                    GatewayErrorCode.BUDGET_NOT_FOUND,
                    "Budget not found.",
                )

            return _snapshot(budget)

    def unknown_fraction_exceeded(self, budget_id: int) -> bool:
        """U >= theta * L (money or tokens): admission must stop."""
        snap = self.snapshot(budget_id)

        return (
            snap.unknown_micro * BPS_DENOMINATOR
            >= self._theta_bps * snap.limit_micro
            or snap.unknown_tokens * BPS_DENOMINATOR
            >= self._theta_bps * snap.limit_tokens
        )

    def get_call(self, call_id: int) -> IntelligenceModelCall:
        with self._tx() as (_, repo):
            call = repo.get_call(call_id)

            if call is None:
                raise GatewayError(
                    GatewayErrorCode.CALL_NOT_FOUND,
                    "Model call not found.",
                )

            return call

    def verify_conservation(self, budget_id: int) -> ConservationReport:
        with self._tx() as (_, repo):
            budget = repo.read_budget_fresh(budget_id)

            if budget is None:
                raise GatewayError(
                    GatewayErrorCode.BUDGET_NOT_FOUND,
                    "Budget not found.",
                )

            stored = {
                "reserved_micro": budget.reserved_micro,
                "reserved_tokens": budget.reserved_tokens,
                "settled_micro": budget.settled_micro,
                "settled_tokens": budget.settled_tokens,
                "unknown_micro": budget.unknown_micro,
                "unknown_tokens": budget.unknown_tokens,
                "overrun_micro": budget.overrun_micro,
                "overrun_tokens": budget.overrun_tokens,
            }
            recomputed = dict.fromkeys(stored, 0)

            for call in repo.list_calls(budget_id=budget_id):
                if call.state in ("reserved", "in_flight"):
                    recomputed["reserved_micro"] += call.reserved_micro
                    recomputed["reserved_tokens"] += (
                        call.reserved_tokens
                    )
                elif call.state == "unknown":
                    recomputed["unknown_micro"] += call.reserved_micro
                    recomputed["unknown_tokens"] += (
                        call.reserved_tokens
                    )
                elif call.state == "settled":
                    alloc_m, over_m = _split(
                        call.actual_micro, call.reserved_micro
                    )
                    alloc_t, over_t = _split(
                        call.actual_tokens, call.reserved_tokens
                    )
                    recomputed["settled_micro"] += alloc_m
                    recomputed["settled_tokens"] += alloc_t
                    recomputed["overrun_micro"] += over_m
                    recomputed["overrun_tokens"] += over_t

            return ConservationReport(
                budget_id=budget_id,
                consistent=stored == recomputed,
                stored=stored,
                recomputed=recomputed,
            )

    # ------------------------------------------------------------------
    # T1  (none) -> reserved
    # ------------------------------------------------------------------

    def reserve(
        self,
        *,
        call_key: str,
        purpose: str,
        provider: str,
        model_profile: str,
        model_id: str,
        reservation: Reservation,
        deadline_seconds: int,
        user_budget_id: int | None = None,
        objective_budget_id: int | None = None,
        deployment_budget_id: int | None = None,
        attempt: int = 1,
        parent_call_id: int | None = None,
        objective_id: int | None = None,
        plan_id: int | None = None,
        prompt_template_id: str | None = None,
        output_schema_id: str | None = None,
        data_classification: DataClassification = (
            DataClassification.SYNTHETIC
        ),
    ) -> int:
        if (
            reservation.micro < 1
            or reservation.tokens < 1
            or deadline_seconds < 1
        ):
            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                "Reservation must be positive.",
            )

        budget_ids = sorted(
            {
                budget_id
                for budget_id in (
                    user_budget_id,
                    objective_budget_id,
                    deployment_budget_id,
                )
                if budget_id is not None
            }
        )

        if not budget_ids:
            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                "At least one budget is required.",
            )

        now = self._clock()

        with self._tx() as (db, repo):
            if repo.get_call_by_key(call_key) is not None:
                raise GatewayError(
                    GatewayErrorCode.DUPLICATE_CALL_KEY,
                    "call_key already used.",
                )

            # BI-2, all-or-nothing: budgets are visited in ascending
            # id order (no lock-order cycles); the first rejection
            # aborts the transaction and rolls back earlier holds.
            for budget_id in budget_ids:
                admitted = repo.try_reserve(
                    budget_id,
                    micro=reservation.micro,
                    tokens=reservation.tokens,
                    now=now,
                    unknown_fraction_bps=self._theta_bps,
                )

                if not admitted:
                    raise self._classify_rejection(repo, budget_id, now)

            try:
                call = repo.add_call(
                    IntelligenceModelCall(
                        call_key=call_key,
                        purpose=purpose,
                        objective_id=objective_id,
                        plan_id=plan_id,
                        parent_call_id=parent_call_id,
                        attempt=attempt,
                        provider=provider,
                        model_profile=model_profile,
                        model_id=model_id,
                        prompt_template_id=prompt_template_id,
                        output_schema_id=output_schema_id,
                        data_classification=data_classification.value,
                        state=CallState.RESERVED.value,
                        user_budget_id=user_budget_id,
                        objective_budget_id=objective_budget_id,
                        deployment_budget_id=deployment_budget_id,
                        reserved_micro=reservation.micro,
                        reserved_tokens=reservation.tokens,
                        reserved_at=now,
                        deadline_at=now
                        + timedelta(seconds=deadline_seconds),
                    )
                )
            except IntegrityError as error:
                constraint = getattr(
                    getattr(error.orig, "diag", None),
                    "constraint_name",
                    None,
                )

                if constraint == "uq_intelligence_model_calls_call_key":
                    raise GatewayError(
                        GatewayErrorCode.DUPLICATE_CALL_KEY,
                        "call_key already used.",
                    ) from error

                raise GatewayError(
                    GatewayErrorCode.INVALID_REQUEST,
                    "Call row rejected by database constraints.",
                ) from error

            return call.id

    def _classify_rejection(
        self,
        repo: IntelligenceBudgetRepository,
        budget_id: int,
        now: datetime,
    ) -> GatewayError:
        budget = repo.read_budget_fresh(budget_id)

        if budget is None:
            return GatewayError(
                GatewayErrorCode.BUDGET_NOT_FOUND,
                "Budget not found.",
            )

        if budget.period_start is not None and not (
            budget.period_start <= now < budget.period_end
        ):
            return GatewayError(
                GatewayErrorCode.BUDGET_NOT_ACTIVE,
                "Budget period is not active.",
            )

        if (
            budget.unknown_micro * BPS_DENOMINATOR
            >= self._theta_bps * budget.limit_micro
            or budget.unknown_tokens * BPS_DENOMINATOR
            >= self._theta_bps * budget.limit_tokens
        ):
            return GatewayError(
                GatewayErrorCode.UNKNOWN_FRACTION_BREAKER,
                "Unknown consumption reached the breaker threshold.",
            )

        return GatewayError(
            GatewayErrorCode.BUDGET_EXCEEDED,
            "Budget would be exceeded.",
        )

    # ------------------------------------------------------------------
    # T2  reserved -> in_flight   (write-ahead mark)
    # ------------------------------------------------------------------

    def mark_in_flight(
        self,
        call_id: int,
        *,
        timeout_seconds: int,
        request_digest: str | None = None,
    ) -> None:
        """
        Commits `in_flight` + sent_at BEFORE any network write. If this
        raises (or the process dies first) nothing was sent and the
        reservation is released by the caller/recovery as never_sent.
        """
        now = self._clock()

        with self._tx() as (_, repo):
            call = self._locked_call(repo, call_id)

            if call.state != CallState.RESERVED.value:
                self._reject(call, "reserved")

            call.state = CallState.IN_FLIGHT.value
            call.sent_at = now
            call.deadline_at = now + timedelta(seconds=timeout_seconds)
            call.request_digest = request_digest
            repo.db.flush()

    # ------------------------------------------------------------------
    # T3  in_flight -> settled
    # ------------------------------------------------------------------

    def settle(
        self,
        call_id: int,
        *,
        actual_micro: int,
        actual_tokens: int,
        response_digest: str | None = None,
    ) -> None:
        if actual_micro < 0 or actual_tokens < 0:
            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                "Actual amounts must be non-negative.",
            )

        now = self._clock()

        with self._tx() as (_, repo):
            call = self._locked_call(repo, call_id)

            if call.state != CallState.IN_FLIGHT.value:
                self._reject(call, "in_flight")

            alloc_m, over_m = _split(actual_micro, call.reserved_micro)
            alloc_t, over_t = _split(
                actual_tokens, call.reserved_tokens
            )
            self._apply(
                repo,
                call,
                now,
                reserved_micro=-call.reserved_micro,
                reserved_tokens=-call.reserved_tokens,
                settled_micro=alloc_m,
                settled_tokens=alloc_t,
                overrun_micro=over_m,
                overrun_tokens=over_t,
            )
            call.state = CallState.SETTLED.value
            call.actual_micro = actual_micro
            call.actual_tokens = actual_tokens
            call.settled_at = now
            call.response_digest = response_digest
            repo.db.flush()

    # ------------------------------------------------------------------
    # T4  in_flight -> unknown
    # ------------------------------------------------------------------

    def mark_unknown(
        self,
        call_id: int,
        *,
        error_class: str,
    ) -> None:
        now = self._clock()

        with self._tx() as (_, repo):
            call = self._locked_call(repo, call_id)

            if call.state != CallState.IN_FLIGHT.value:
                self._reject(call, "in_flight")

            self._to_unknown(repo, call, now, error_class)

    def _to_unknown(
        self,
        repo: IntelligenceBudgetRepository,
        call: IntelligenceModelCall,
        now: datetime,
        error_class: str,
    ) -> None:
        self._apply(
            repo,
            call,
            now,
            reserved_micro=-call.reserved_micro,
            reserved_tokens=-call.reserved_tokens,
            unknown_micro=call.reserved_micro,
            unknown_tokens=call.reserved_tokens,
        )
        call.state = CallState.UNKNOWN.value
        call.unknown_at = now
        call.error_class = error_class[:64]
        repo.db.flush()

    # ------------------------------------------------------------------
    # T5  reserved|in_flight -> released (never_sent)
    # ------------------------------------------------------------------

    def release_never_sent(
        self,
        call_id: int,
        *,
        evidence_ref: str | None = None,
        error_class: str | None = None,
    ) -> None:
        """
        From `reserved`: local proof (the in_flight mark never
        committed). From `in_flight`: ONLY with adapter-attested proof
        that nothing was transmitted -- evidence_ref is then mandatory
        (also enforced by a CHECK).
        """
        now = self._clock()

        with self._tx() as (_, repo):
            call = self._locked_call(repo, call_id)
            self._release_never_sent(
                repo, call, now, evidence_ref, error_class
            )

    def _release_never_sent(
        self,
        repo: IntelligenceBudgetRepository,
        call: IntelligenceModelCall,
        now: datetime,
        evidence_ref: str | None,
        error_class: str | None,
    ) -> None:
        if call.state == CallState.IN_FLIGHT.value:
            if not evidence_ref or not evidence_ref.strip():
                raise GatewayError(
                    GatewayErrorCode.EVIDENCE_REQUIRED,
                    "Releasing an in_flight call needs not-sent "
                    "evidence.",
                )
        elif call.state != CallState.RESERVED.value:
            self._reject(call, "reserved or in_flight")

        self._apply(
            repo,
            call,
            now,
            reserved_micro=-call.reserved_micro,
            reserved_tokens=-call.reserved_tokens,
        )
        call.state = CallState.RELEASED.value
        call.release_basis = ReleaseBasis.NEVER_SENT.value
        call.released_at = now
        call.evidence_ref = (
            evidence_ref[:256] if evidence_ref else None
        )
        call.error_class = error_class[:64] if error_class else None
        repo.db.flush()

    # ------------------------------------------------------------------
    # T6  in_flight -> released (documented provider charge policy)
    # ------------------------------------------------------------------

    def release_by_provider_policy(
        self,
        call_id: int,
        *,
        error_class: str,
    ) -> None:
        now = self._clock()

        with self._tx() as (_, repo):
            call = self._locked_call(repo, call_id)

            if call.state != CallState.IN_FLIGHT.value:
                self._reject(call, "in_flight")

            reference = PROVIDER_NO_CHARGE_POLICIES.get(
                (call.provider, error_class)
            )

            if not reference:
                raise GatewayError(
                    GatewayErrorCode.EVIDENCE_REQUIRED,
                    "No documented no-charge policy for this "
                    "provider/error.",
                )

            self._apply(
                repo,
                call,
                now,
                reserved_micro=-call.reserved_micro,
                reserved_tokens=-call.reserved_tokens,
            )
            call.state = CallState.RELEASED.value
            call.release_basis = ReleaseBasis.PROVIDER_DOCUMENTED.value
            call.released_at = now
            call.evidence_ref = reference[:256]
            call.error_class = error_class[:64]
            repo.db.flush()

    # ------------------------------------------------------------------
    # T7 / T8  unknown -> settled | released   (admin reconciliation)
    # ------------------------------------------------------------------

    def reconcile_unknown_settled(
        self,
        call_id: int,
        *,
        actual_micro: int,
        actual_tokens: int,
        evidence_ref: str,
        reconciled_by_reference: str,
    ) -> None:
        self._require_reconciliation_evidence(
            evidence_ref, reconciled_by_reference
        )

        if actual_micro < 0 or actual_tokens < 0:
            raise GatewayError(
                GatewayErrorCode.INVALID_REQUEST,
                "Actual amounts must be non-negative.",
            )

        now = self._clock()

        with self._tx() as (_, repo):
            call = self._locked_call(repo, call_id)

            if call.state != CallState.UNKNOWN.value:
                self._reject(call, "unknown")

            alloc_m, over_m = _split(actual_micro, call.reserved_micro)
            alloc_t, over_t = _split(
                actual_tokens, call.reserved_tokens
            )
            self._apply(
                repo,
                call,
                now,
                unknown_micro=-call.reserved_micro,
                unknown_tokens=-call.reserved_tokens,
                settled_micro=alloc_m,
                settled_tokens=alloc_t,
                overrun_micro=over_m,
                overrun_tokens=over_t,
            )
            call.state = CallState.SETTLED.value
            call.actual_micro = actual_micro
            call.actual_tokens = actual_tokens
            call.settled_at = now
            call.evidence_ref = evidence_ref[:256]
            call.reconciled_by_reference = reconciled_by_reference[:128]
            call.reconciled_at = now
            repo.db.flush()

    def reconcile_unknown_released(
        self,
        call_id: int,
        *,
        evidence_ref: str,
        reconciled_by_reference: str,
    ) -> None:
        self._require_reconciliation_evidence(
            evidence_ref, reconciled_by_reference
        )
        now = self._clock()

        with self._tx() as (_, repo):
            call = self._locked_call(repo, call_id)

            if call.state != CallState.UNKNOWN.value:
                self._reject(call, "unknown")

            self._apply(
                repo,
                call,
                now,
                unknown_micro=-call.reserved_micro,
                unknown_tokens=-call.reserved_tokens,
            )
            call.state = CallState.RELEASED.value
            call.release_basis = ReleaseBasis.ADMIN_RECONCILIATION.value
            call.released_at = now
            call.evidence_ref = evidence_ref[:256]
            call.reconciled_by_reference = reconciled_by_reference[:128]
            call.reconciled_at = now
            repo.db.flush()

    @staticmethod
    def _require_reconciliation_evidence(
        evidence_ref: str | None,
        reconciled_by_reference: str | None,
    ) -> None:
        if (
            not evidence_ref
            or not evidence_ref.strip()
            or not reconciled_by_reference
            or not reconciled_by_reference.strip()
        ):
            raise GatewayError(
                GatewayErrorCode.EVIDENCE_REQUIRED,
                "Reconciling unknown consumption needs an "
                "evidence reference and an authorised reconciler.",
            )

    # ------------------------------------------------------------------
    # restart recovery
    # ------------------------------------------------------------------

    def recover(self, now: datetime | None = None) -> RecoveryReport:
        """
        Resolves calls abandoned by a crashed/restarted process.

          reserved past deadline (+grace) -> released(never_sent):
              the in_flight mark never committed, so nothing was sent.
          in_flight past deadline (+grace) -> unknown:
              it MAY have been sent; never released, never settled by
              guess (BI-5).

        Idempotent: each row is re-validated under its row lock, so
        running recover() twice (or concurrently) changes nothing the
        second time.
        """
        current = now or self._clock()
        cutoff = current - self._grace
        released: list[int] = []
        unknown: list[int] = []

        with self._tx() as (_, repo):
            reserved_ids = repo.list_call_ids_due(
                state=CallState.RESERVED.value,
                deadline_before=cutoff,
            )
            in_flight_ids = repo.list_call_ids_due(
                state=CallState.IN_FLIGHT.value,
                deadline_before=cutoff,
            )

        for call_id in reserved_ids:
            with self._tx() as (_, repo):
                call = self._locked_call(repo, call_id)

                if (
                    call.state == CallState.RESERVED.value
                    and call.deadline_at < cutoff
                ):
                    self._release_never_sent(
                        repo,
                        call,
                        current,
                        None,
                        "crash_before_send",
                    )
                    released.append(call_id)

        for call_id in in_flight_ids:
            with self._tx() as (_, repo):
                call = self._locked_call(repo, call_id)

                if (
                    call.state == CallState.IN_FLIGHT.value
                    and call.deadline_at < cutoff
                ):
                    self._to_unknown(
                        repo, call, current, "crash_after_send"
                    )
                    unknown.append(call_id)

        return RecoveryReport(
            released_never_sent=tuple(released),
            marked_unknown=tuple(unknown),
        )
