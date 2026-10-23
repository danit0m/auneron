"""AI-PLAN-04: projection-to-request preparation stays offline."""
from app.intelligence.gateway.contracts import DataClassification
from intelligence_pipeline_preparation import prepare_proposal
from test_intelligence_projection_contracts import base_request


def test_preparation_is_pure_and_never_dispatches():
    prepared = prepare_proposal(
        base_request(), call_key="ai-plan-04-synthetic",
        input_text="Synthetic objective reference only.",
        max_output_tokens=128,
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


def test_invalid_preparation_does_not_create_model_request():
    for tokens in (0, 513, True):
        prepared = prepare_proposal(
            base_request(), call_key="ai-plan-04-synthetic",
            input_text="Synthetic.", max_output_tokens=tokens,
        )
        assert prepared.model_request is None


def test_adversarial_request_boundaries_fail_closed():
    for call_key, text, tokens in (
        ("", "Synthetic.", 128),
        ("x" * 121, "Synthetic.", 128),
        ("valid-key", "", 128),
        ("valid-key", "x" * 32769, 128),
        ("valid-key", "Synthetic.", 513),
        ("valid-key", "Synthetic.", False),
    ):
        prepared = prepare_proposal(
            base_request(), call_key=call_key,
            input_text=text, max_output_tokens=tokens,
        )
        assert prepared.model_request is None


def test_unicode_byte_limit_is_enforced():
    prepared = prepare_proposal(
        base_request(), call_key="valid-key",
        input_text="á" * 16385, max_output_tokens=128,
    )
    assert prepared.model_request is None


def test_failed_projection_cannot_create_model_request():
    prepared = prepare_proposal(
        base_request(account_refs=("acct_missing",)),
        call_key="ai-plan-04-synthetic",
        input_text="Synthetic.", max_output_tokens=128,
    )
    assert prepared.projection_result.projection is None
    assert prepared.model_request is None
