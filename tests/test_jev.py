"""Jev's calls: held and settled at Jev's price, and only sound answers get through."""

import json
from datetime import date, datetime
from typing import Any

import httpx
import psycopg
import pytest

from fakes import MemoryLedger, ScriptedJev, fake_jev, pick, yes
from rockygpt_brain.jev import (
    CALL_TOKENS,
    JEV_PRICE,
    JEV_URL,
    Jev,
    JevError,
    Pick,
    Price,
    TypesafeHttp,
    Yes,
    choice,
    noul,
    token_bound,
)
from rockygpt_brain.spending import CAMPUS_TIMEZONE, PostgresLedger

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=CAMPUS_TIMEZONE)
STATE = {"latest_request": "When does Birch close?"}
QUESTIONS = {
    "hours": noul("Does `latest_request` ask when a place is open?", "Asks when", "Asks else"),
    "meal": choice("Which meal?", {"none": "No meal", "lunch": "Lunch"}),
}
ANSWERS = {"hours": yes(0.97), "meal": pick("none", {"none": 0.9, "lunch": 0.1}, 0.88)}


def test_the_price_is_the_one_checked_on_typesafes_page() -> None:
    assert (JEV_PRICE.model, JEV_PRICE.input_nusd) == ("jev-1.13.0", 42)
    assert JEV_PRICE.source == "https://docs.typesafe.ai/models"


def test_a_price_is_trusted_only_inside_its_window() -> None:
    price = Price("jev-1.13.0", 42, date(2026, 9, 22), date(2026, 10, 22), "https://x")
    assert price.current(datetime(2026, 10, 21, 23, 59, tzinfo=CAMPUS_TIMEZONE))
    assert not price.current(datetime(2026, 10, 22, 0, 0, tzinfo=CAMPUS_TIMEZONE))
    assert not price.current(datetime(2026, 9, 21, 23, 59, tzinfo=CAMPUS_TIMEZONE))


def test_a_call_holds_its_worst_case_and_settles_what_it_used() -> None:
    jev, script, ledger = fake_jev(ScriptedJev(ANSWERS, input_tokens=500))
    asked = jev.ask("r1", STATE, QUESTIONS, NOW)
    assert asked.answers == {
        "hours": Yes(0.97),
        "meal": Pick("none", 0.9, 0.88, {"none": 0.9, "lunch": 0.1}),
    }
    assert asked.cost_nusd == 500 * 42
    (hold,) = ledger.holds.values()
    assert hold == {"state": "settled", "amount": token_bound(script.sent[0]) * 42,
                    "category": "routing", "cost": 500 * 42, "model": "jev-1.13.0"}
    assert script.sent[0] == {"model": "jev-1.13.0", "state": STATE, "questions": QUESTIONS}


def test_an_expired_price_skips_jev_before_any_hold() -> None:
    expired = Price("jev-1.13.0", 42, date(2026, 8, 1), date(2026, 9, 1), "https://x")
    script, ledger = ScriptedJev(ANSWERS), MemoryLedger()
    with pytest.raises(JevError) as skipped:
        Jev(script, ledger, expired).ask("r1", STATE, QUESTIONS, NOW)
    assert (skipped.value.code, skipped.value.sent) == ("routing_price_unavailable", False)
    assert not ledger.holds and not script.sent


def test_a_call_too_long_for_jev_is_not_sent() -> None:
    jev, script, ledger = fake_jev(ScriptedJev(ANSWERS))
    with pytest.raises(JevError) as skipped:
        jev.ask("r1", {"latest_request": "x" * CALL_TOKENS}, QUESTIONS, NOW)
    assert (skipped.value.code, skipped.value.sent) == ("routing_context_limit", False)
    assert not ledger.holds and not script.sent


def test_a_call_that_times_out_stays_held_as_uncertain() -> None:
    jev, _, ledger = fake_jev(ScriptedJev(error=JevError("routing_timeout")))
    with pytest.raises(JevError) as failed:
        jev.ask("r1", STATE, QUESTIONS, NOW)
    assert failed.value.sent
    (hold,) = ledger.holds.values()
    assert (hold["state"], hold["code"]) == ("uncertain", "routing_timeout")


def test_another_model_is_paid_for_but_not_trusted() -> None:
    jev, _, ledger = fake_jev(ScriptedJev(ANSWERS, model="jev-2.0.0"))
    with pytest.raises(JevError) as changed:
        jev.ask("r1", STATE, QUESTIONS, NOW)
    assert changed.value.code == "routing_model_changed"
    assert next(iter(ledger.holds.values()))["state"] == "settled"


@pytest.mark.parametrize("answers", [
    {"hours": yes(0.97)},
    {**ANSWERS, "extra": yes(0.5)},
    {**ANSWERS, "hours": yes(1.2)},
    {**ANSWERS, "hours": yes(True)},
    {**ANSWERS, "hours": pick("none", {"none": 0.9, "lunch": 0.1})},
    {**ANSWERS, "meal": yes(0.5)},
    {**ANSWERS, "meal": pick("dinner", {"none": 0.9, "lunch": 0.1})},
    {**ANSWERS, "meal": pick("lunch", {"none": 0.9, "lunch": 0.1})},
    {**ANSWERS, "meal": pick("none", {"none": 0.9, "lunch": 0.3})},
    {**ANSWERS, "meal": pick("none", {"none": 0.9})},
    {**ANSWERS, "meal": pick("none", {"none": 0.9, "lunch": 0.1}, confidence=-0.1)},
    ["not", "a", "map"],
])
def test_an_unsound_answer_is_thrown_away(answers: Any) -> None:
    jev, _, _ = fake_jev(ScriptedJev(answers))
    with pytest.raises(JevError) as unsound:
        jev.ask("r1", STATE, QUESTIONS, NOW)
    assert unsound.value.code == "routing_invalid_response"


def test_probabilities_rounded_to_the_hundredth_are_sound() -> None:
    three = {"meal": choice("Which meal?", {"none": "-", "lunch": "-", "dinner": "-"})}
    jev, _, _ = fake_jev(ScriptedJev(
        {"meal": pick("none", {"none": 0.54, "lunch": 0.32, "dinner": 0.13})}))
    assert jev.ask("r1", STATE, three, NOW).answers["meal"].probability == 0.54


def typesafe(handler: Any) -> TypesafeHttp:
    return TypesafeHttp("test-key", httpx.Client(transport=httpx.MockTransport(handler)))


BODY = {"model": "jev-1.13.0", "state": STATE, "questions": QUESTIONS}


def test_typesafe_gets_the_call_with_the_key_and_reports_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == JEV_URL
        assert request.headers["authorization"] == "Bearer test-key"
        assert json.loads(request.content) == BODY
        return httpx.Response(200, json={"id": "abc", "model": "jev-1.13.0",
                                         "answers": ANSWERS,
                                         "usage": {"input_tokens": 812, "output_tokens": 9}})

    reply = typesafe(handler)(BODY, 2.0)
    assert (reply.input_tokens, reply.output_tokens, reply.model, reply.response_id) == (
        812, 9, "jev-1.13.0", "abc")
    assert reply.answers == ANSWERS


@pytest.mark.parametrize(("response", "code"), [
    (httpx.Response(429), "routing_rate_limited"),
    (httpx.Response(401), "routing_provider_error"),
    (httpx.Response(529), "routing_provider_error"),
    (httpx.Response(200, json={"answers": ANSWERS}), "routing_usage_unknown"),
    (httpx.Response(200, json={"answers": ANSWERS, "usage": {"input_tokens": -1,
                                                             "output_tokens": 0}}),
     "routing_usage_unknown"),
    (httpx.Response(200, content=b"not json"), "routing_usage_unknown"),
    (httpx.Response(200, content=b"x" * 1_100_000), "routing_invalid_response"),
])
def test_typesafe_trouble_is_named(response: httpx.Response, code: str) -> None:
    with pytest.raises(JevError) as trouble:
        typesafe(lambda request: response)(BODY, 2.0)
    assert trouble.value.code == code


@pytest.mark.parametrize(("error", "code"), [
    (httpx.ReadTimeout("slow"), "routing_timeout"),
    (httpx.ConnectError("down"), "routing_unavailable"),
])
def test_typesafe_out_of_reach_is_named(error: Exception, code: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    with pytest.raises(JevError) as trouble:
        typesafe(handler)(BODY, 2.0)
    assert trouble.value.code == code


def test_a_call_is_settled_in_the_real_ledger(admin: psycopg.Connection[Any],
                                             database: str) -> None:
    script = ScriptedJev(ANSWERS, input_tokens=2000)
    Jev(script, PostgresLedger(database, "development")).ask("r1", STATE, QUESTIONS, NOW)
    row = admin.execute("SELECT category, state, reserved_nusd, cost_nusd, usage, returned_model "
                        "FROM brain_ops.operations").fetchone()
    assert row == ("routing", "settled", token_bound(script.sent[0]) * 42, 84_000,
                   {"input_tokens": 2000, "output_tokens": 0}, "jev-1.13.0")
