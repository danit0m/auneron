"""
T-5 -- coerencia temporal (nada antes da emissao; baixa >= evidencia >=
pagamento; relogio so avanca; janela D0..D179).
T-6 -- calendario normativo (boleto nao compensa em dia nao util; PIX sim).
"""

from __future__ import annotations

from datetime import date

from sim.generator.timeline import to_minutes


def _key(instant):
    return (instant["day"], to_minutes(instant["at"]))


def test_t5_title_chronology(scenario):
    for title in scenario.world["titles"]:
        issue = _key(title["issue"])
        history = [(h["day"], to_minutes(h["at"])) for h in title["due_history"]]
        assert history[0] == issue
        assert history == sorted(history)
        if title["fact_at"]:
            assert _key(title["fact_at"]) > issue
        if title["partial"]:
            assert issue < _key(title["partial"]) < _key(title["fact_at"])
        if title["evidence_at"]:
            assert _key(title["evidence_at"]) >= _key(title["fact_at"])
        if title["reported_at"]:
            assert _key(title["reported_at"]) >= _key(title["evidence_at"])
        for contact in title["contacts"]:
            assert _key(contact) > issue


def test_t5_agenda_order_and_window(scenario):
    previous = (-1, -1)
    created = set()
    for op in scenario.agenda["ops"]:
        current = (op["day"], to_minutes(op["at"]))
        assert current >= previous
        previous = current
        assert 0 <= op["day"] <= 179
        if op["op"] == "create_receivable":
            created.add(op["rec_ref"])
        elif "rec_ref" in op:
            assert op["rec_ref"] in created, op


def test_t5_world_events_in_window(scenario):
    previous = (-1, -1)
    for event in scenario.world["events"]:
        current = (event["day"], to_minutes(event["at"]))
        assert current >= previous
        previous = current
        assert 0 <= event["day"] <= 179


def test_t5_d0_d179_semantics(inputs):
    cal = inputs.calendar
    assert cal.days == 180
    assert cal.iso(0) == "2026-01-05"
    assert cal.iso(cal.last_day) == "2026-07-03"
    assert cal.date_of(0).weekday() == 0


def test_t6_normative_calendar(inputs):
    cal = inputs.calendar
    declared = {cal.iso(day) for day in cal.non_business_days}
    assert declared == {
        "2026-02-16", "2026-02-17", "2026-04-03", "2026-04-21", "2026-05-01", "2026-06-04",
    }
    assert "Nao reproduz" in inputs.calendar_raw["normative_notice"]
    windows = [(cal.iso(lo), cal.iso(hi)) for lo, hi in cal.seasonal_due_windows]
    assert windows == [("2026-03-25", "2026-04-10"), ("2026-06-25", "2026-07-03")]


def test_t6_payment_methods_follow_calendar(scenario, inputs):
    cal = inputs.calendar
    for title in scenario.world["titles"]:
        fact, evidence = title["fact_at"], title["evidence_at"]
        if fact is None or evidence is None:
            continue
        if title["method"] == "boleto":
            assert cal.is_business(evidence["day"])
            assert evidence["at"] == "07:00"
            assert evidence["day"] > fact["day"]
        elif title["method"] == "ted":
            assert cal.is_business(fact["day"])
            assert evidence == fact
        else:
            assert evidence == fact


def test_t6_holiday_due_paid_next_business_day(generated, inputs):
    cal = inputs.calendar
    oracle = generated["nh-standard"].oracle
    titles = {t["rec_ref"]: t for t in generated["nh-standard"].world["titles"]}
    for ref in oracle["case_instances"]["C-NEG-3"]:
        title = titles[ref]
        due = title["due_history"][-1]["due_day"]
        assert not cal.is_business(due)
        assert title["fact_at"]["day"] == cal.effective_due(due)
    assert date(2026, 2, 16) == cal.date_of(42)
