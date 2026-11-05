"""AI-PLAN-04: projection-to-request preparation stays offline."""
import ast
from pathlib import Path

from app.intelligence.gateway.contracts import DataClassification
from intelligence_pipeline_preparation import (
    MAX_CALL_KEY_LENGTH,
    MAX_REQUEST_BYTES,
    PREPARATION_REJECTION_REASONS,
    PreparationRejection,
    PreparedProposal,
    prepare_proposal,
)
from test_intelligence_projection_contracts import base_request


def test_preparation_is_pure_and_never_dispatches():
    prepared = prepare_proposal(
        base_request(), call_key="ai-plan-04-synthetic",
        input_text="Synthetic objective reference only.",
        max_output_tokens=128, deployment_budget_id=1,
    )
    assert prepared.projection_result.ok
    model = prepared.model_request
    projection = prepared.projection_result.projection
    assert model is not None and projection is not None
    assert model.data_classification is DataClassification.SYNTHETIC
    assert model.output_schema == projection.output_schema
    assert model.instructions == projection.instructions
    assert model.profile == projection.profile_name
    assert model.output_schema_id == projection.schema_digest
    assert model.budget_ids == (1,)


def test_invalid_preparation_does_not_create_model_request():
    for tokens in (0, 513, True):
        prepared = prepare_proposal(
            base_request(), call_key="ai-plan-04-synthetic",
            input_text="Synthetic.", max_output_tokens=tokens,
            deployment_budget_id=1,
        )
        assert prepared.model_request is None


def test_adversarial_request_boundaries_fail_closed():
    for call_key, text, tokens in (
        ("", "Synthetic.", 128),
        ("   ", "Synthetic.", 128),
        ("x" * 121, "Synthetic.", 128),
        ("valid-key", "", 128),
        ("valid-key", "x" * 32769, 128),
        ("valid-key", "Synthetic.", 513),
        ("valid-key", "Synthetic.", False),
    ):
        prepared = prepare_proposal(
            base_request(), call_key=call_key,
            input_text=text, max_output_tokens=tokens,
            deployment_budget_id=1,
        )
        assert prepared.model_request is None


def test_unicode_byte_limit_is_enforced():
    prepared = prepare_proposal(
        base_request(), call_key="valid-key",
        input_text="á" * 16385, max_output_tokens=128,
        deployment_budget_id=1,
    )
    assert prepared.model_request is None


def test_no_budget_identifier_does_not_create_model_request():
    prepared = prepare_proposal(
        base_request(), call_key="ai-plan-04-synthetic",
        input_text="Synthetic.", max_output_tokens=128,
    )
    assert prepared.projection_result.ok
    assert prepared.model_request is None


def test_any_single_budget_identifier_is_sufficient():
    for kwargs in (
        {"user_budget_id": 7},
        {"objective_budget_id": 7},
        {"deployment_budget_id": 7},
    ):
        prepared = prepare_proposal(
            base_request(), call_key="ai-plan-04-synthetic",
            input_text="Synthetic.", max_output_tokens=128,
            **kwargs,
        )
        assert prepared.model_request is not None
        assert prepared.model_request.budget_ids == (7,)


def test_failed_projection_cannot_create_model_request():
    prepared = prepare_proposal(
        base_request(account_refs=("acct_missing",)),
        call_key="ai-plan-04-synthetic",
        input_text="Synthetic.", max_output_tokens=128,
        deployment_budget_id=1,
    )
    assert prepared.projection_result.projection is None
    assert prepared.model_request is None


# AI-PLAN-12-P1B: structured rejection reasons.

def _prepare(**overrides):
    kwargs = {
        "call_key": "ai-plan-12-p1b-synthetic",
        "input_text": "Synthetic.",
        "max_output_tokens": 128,
        "deployment_budget_id": 1,
    }
    kwargs.update(overrides)
    request = kwargs.pop("request", None) or base_request()
    return prepare_proposal(request, **kwargs)


def test_accepted_preparation_has_no_rejection_reason():
    prepared = _prepare()
    assert prepared.model_request is not None
    assert prepared.rejection_reason is None


def test_each_rejection_path_maps_to_its_reason_code():
    R = PreparationRejection
    cases = [
        ({"request": base_request(account_refs=("acct_missing",))}, R.PROJECTION_REJECTED),
        ({"call_key": ""}, R.INVALID_CALL_KEY),
        ({"call_key": "   "}, R.INVALID_CALL_KEY),
        ({"call_key": "x" * 121}, R.INVALID_CALL_KEY),
        ({"call_key": None}, R.INVALID_CALL_KEY),
        ({"input_text": ""}, R.INVALID_INPUT_TEXT),
        ({"input_text": b"Synthetic."}, R.INVALID_INPUT_TEXT),
        ({"max_output_tokens": 0}, R.INVALID_MAX_OUTPUT_TOKENS),
        ({"max_output_tokens": 513}, R.INVALID_MAX_OUTPUT_TOKENS),
        ({"max_output_tokens": True}, R.INVALID_MAX_OUTPUT_TOKENS),
        ({"max_output_tokens": 128.0}, R.INVALID_MAX_OUTPUT_TOKENS),
        ({"input_text": "x" * 32769}, R.REQUEST_TOO_LARGE),
        ({"input_text": "á" * 16385}, R.REQUEST_TOO_LARGE),
        ({"deployment_budget_id": None}, R.MISSING_BUDGET_ID),
    ]
    for overrides, expected in cases:
        prepared = _prepare(**overrides)
        assert prepared.model_request is None, overrides
        assert prepared.rejection_reason == expected, overrides


def test_first_failing_check_wins_deterministically():
    R = PreparationRejection
    for overrides, expected in (
        ({"call_key": "", "input_text": "", "deployment_budget_id": None}, R.INVALID_CALL_KEY),
        ({"input_text": "", "max_output_tokens": 0}, R.INVALID_INPUT_TEXT),
        ({"max_output_tokens": 0, "deployment_budget_id": None}, R.INVALID_MAX_OUTPUT_TOKENS),
        ({"input_text": "x" * 32769, "deployment_budget_id": None}, R.REQUEST_TOO_LARGE),
    ):
        first = _prepare(**overrides)
        second = _prepare(**overrides)
        assert first.rejection_reason == second.rejection_reason == expected


def test_rejection_reason_never_echoes_request_data():
    marker = "p1b-marker-value"
    for overrides in (
        {"call_key": marker + "x" * 121},
        {"call_key": " ", "input_text": marker},
        {"input_text": marker * 4000},
        {"input_text": marker, "max_output_tokens": 0},
    ):
        prepared = _prepare(**overrides)
        assert prepared.rejection_reason in PREPARATION_REJECTION_REASONS
        assert marker not in prepared.rejection_reason


def test_reason_codes_are_fixed_literals():
    assert PREPARATION_REJECTION_REASONS == {
        "projection_rejected",
        "invalid_call_key",
        "invalid_input_text",
        "invalid_max_output_tokens",
        "request_too_large",
        "missing_budget_id",
    }


def test_prepared_proposal_stays_backward_compatible():
    prepared = _prepare()
    legacy = PreparedProposal(prepared.projection_result, prepared.model_request)
    assert legacy.rejection_reason is None
    assert legacy == prepared


def test_request_size_bound_includes_projection_instructions():
    instructions = prepare_proposal(
        base_request(), call_key="size-probe", input_text="x",
        max_output_tokens=128, deployment_budget_id=1,
    ).model_request.instructions
    room = MAX_REQUEST_BYTES - len(instructions.encode("utf-8"))
    fits = _prepare(input_text="x" * room)
    over = _prepare(input_text="x" * (room + 1))
    assert fits.model_request is not None
    assert over.model_request is None
    assert over.rejection_reason == PreparationRejection.REQUEST_TOO_LARGE


def test_mirrored_limits_match_the_gateway_source():
    # gateway.py pulls in the database layer, so it is parsed, never imported.
    gateway = Path(__file__).resolve().parents[1] / (
        "app/intelligence/gateway/gateway.py"
    )
    limits = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(gateway.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"MAX_CALL_KEY_LENGTH", "MAX_REQUEST_BYTES"}
    }
    assert limits == {
        "MAX_CALL_KEY_LENGTH": MAX_CALL_KEY_LENGTH,
        "MAX_REQUEST_BYTES": MAX_REQUEST_BYTES,
    }
