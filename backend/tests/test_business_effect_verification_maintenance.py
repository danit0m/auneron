from types import SimpleNamespace

from app.core import business_effect_verification_maintenance


class _FakeSession:
    def __init__(self) -> None:
        self.rollback_count = 0
        self.closed = False

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.closed = True


class _FakeRepository:
    def __init__(self, ids) -> None:
        self.ids = list(ids)

    def list_recovery_candidate_consumption_ids(self, *, limit):
        return list(self.ids)


def _outcome(consumption_id, result, *, duplicate=False):
    return SimpleNamespace(
        verification=SimpleNamespace(
            id=1000 + consumption_id,
            skill_key="account.mark_overdue",
            result=result,
        ),
        duplicate=duplicate,
    )


def test_recovery_verifies_candidates_and_never_dispatches() -> None:
    db = _FakeSession()
    repository = _FakeRepository([1, 2, 3, 4])

    class Service:
        def __init__(self):
            self.verified = []

        def verify(self, consumption_id):
            self.verified.append(consumption_id)
            mapping = {
                1: "verified",
                2: "contradicted",
                3: "pending",
                4: "unverifiable",
            }
            return _outcome(
                consumption_id, mapping[consumption_id]
            )

        def dispatch(self, *args, **kwargs):
            raise AssertionError(
                "maintenance cannot dispatch"
            )

        def execute(self, *args, **kwargs):
            raise AssertionError(
                "maintenance cannot execute a Skill"
            )

    service = Service()
    summary = (
        business_effect_verification_maintenance
        .run_business_effect_verification_recovery(
            limit=10,
            session_factory=lambda: db,
            repository_factory=lambda session: repository,
            service_factory=lambda session: service,
        )
    )

    assert service.verified == [1, 2, 3, 4]
    assert summary.candidate_count == 4
    assert summary.verified_count == 1
    assert summary.contradicted_count == 1
    assert summary.pending_count == 1
    assert summary.unverifiable_count == 1
    assert summary.failure_count == 0
    assert db.closed is True


def test_recovery_continues_after_failure() -> None:
    db = _FakeSession()
    repository = _FakeRepository([1, 2])

    class Service:
        def verify(self, consumption_id):
            if consumption_id == 1:
                raise RuntimeError("simulated")
            return _outcome(consumption_id, "verified")

    service = Service()
    summary = (
        business_effect_verification_maintenance
        .run_business_effect_verification_recovery(
            limit=10,
            session_factory=lambda: db,
            repository_factory=lambda session: repository,
            service_factory=lambda session: service,
        )
    )

    assert summary.candidate_count == 2
    assert summary.verified_count == 1
    assert summary.failure_count == 1
    assert db.rollback_count == 1
    assert db.closed is True


def test_recovery_rejects_invalid_limit() -> None:
    db = _FakeSession()
    repository = _FakeRepository([])

    class Service:
        def verify(self, consumption_id):
            raise AssertionError(
                "should not be called with empty candidates"
            )

    caught = None
    try:
        business_effect_verification_maintenance.run_business_effect_verification_recovery(
            limit=0,
            session_factory=lambda: db,
            repository_factory=lambda session: repository,
            service_factory=lambda session: Service(),
        )
    except ValueError as error:
        caught = error

    assert caught is not None


def test_recovery_uses_configured_batch_size_when_limit_omitted() -> None:
    from app.core.config import settings

    db = _FakeSession()
    seen_limit = {}

    class Repository:
        def list_recovery_candidate_consumption_ids(
            self, *, limit
        ):
            seen_limit["limit"] = limit
            return []

    class Service:
        def verify(self, consumption_id):
            raise AssertionError("no candidates expected")

    summary = (
        business_effect_verification_maintenance
        .run_business_effect_verification_recovery(
            session_factory=lambda: db,
            repository_factory=lambda session: Repository(),
            service_factory=lambda session: Service(),
        )
    )

    assert (
        seen_limit["limit"]
        == settings.work_skill_recovery_batch_size
    )
    assert summary.candidate_count == 0


def test_recovery_summary_has_no_learning_or_memory_side_effects() -> None:
    """
    Guardrail DW-3: o worker de recovery nunca deve tocar
    WorkOutcomeEvaluation/MemoryItem/learning_signal. Prova isso de
    forma estrutural -- o modulo de manutencao nao importa nenhum dos
    tres simbolos.
    """
    import inspect

    source = inspect.getsource(
        business_effect_verification_maintenance
    )
    for forbidden in (
        "WorkOutcomeEvaluation",
        "MemoryItem",
        "learning_signal",
    ):
        assert forbidden not in source
