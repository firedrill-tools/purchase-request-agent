import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest
from purchase_request.store import Store, strict_json
from purchase_request.schema import validate_state, Recommendation
from purchase_request.crew import handoff

FIXTURE = Path(__file__).parent.parent / "fixtures/starter.json"


def setup(tmp_path):
    file = tmp_path / "state.json"
    file.write_bytes(FIXTURE.read_bytes())
    return Store(file)


def approved(store):
    assert store.propose("PR-101", "Q-201", 1400, 1)["ok"]
    assert store.decide("PROP-1", "Ava Chen", "approved")["ok"]


@pytest.mark.parametrize(
    "field", ["quantity", "total", "source", "decision", "approval"]
)
def test_saved_proposal_and_decision_tampering_is_not_repaired_or_committed(
    tmp_path, field
):
    store = setup(tmp_path)
    approved(store)
    state = store.read()
    p = state["proposals"][0]
    if field == "quantity":
        p["quantity"] = 1
        p["total_cents"] = 1400
    if field == "total":
        p["total_cents"] = 1400
    if field == "source":
        p["source"]["request"]["quantity"] = 1
    if field == "decision":
        p["decision"]["proposal_sha256"] = "0" * 64
    if field == "approval":
        p["approval"] = "rejected"
    store.path.write_text(json.dumps(state))
    original = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.commit("PROP-1")
    assert store.path.read_bytes() == original


@pytest.mark.parametrize(
    "field,error",
    [
        ("sku", "quote_changed"),
        ("quantity", "request_changed"),
        ("cheaper", "quote_population_changed"),
        ("currency", "quote_changed"),
    ],
)
def test_same_revision_source_and_competing_quote_changes_block_order(
    tmp_path, field, error
):
    store = setup(tmp_path)
    approved(store)
    state = store.read()
    if field == "sku":
        state["quotes"][0]["sku"] = "OTHER-SKU"
    if field == "quantity":
        state["requests"][0]["quantity"] = 1
    if field == "cheaper":
        state["quotes"][2]["unit_cents"] = 1000
    if field == "currency":
        state["quotes"][0]["currency"] = "USD"
    store.path.write_text(json.dumps(state))
    assert store.commit("PROP-1")["error"] == error
    assert store.read()["orders"] == []


@pytest.mark.parametrize("value", [True, 1.5, 0, -1, 2**53])
def test_direct_quote_price_and_revision_types_are_strict(tmp_path, value):
    store = setup(tmp_path)
    before = store.path.read_bytes()
    assert not store.update_quote("Q-201", unit_cents=value)["ok"]
    assert not store.propose("PR-101", "Q-201", 1400, value)["ok"]
    assert store.path.read_bytes() == before


def test_strict_source_duplicate_json_and_reference_contract(tmp_path):
    store = setup(tmp_path)
    original = store.read()
    for kind in ("duplicate", "bool", "unknown", "orphan", "nan"):
        state = json.loads(json.dumps(original))
        if kind == "duplicate":
            state["requests"].append(state["requests"][0])
        if kind == "bool":
            state["quotes"][0]["stock"] = True
        if kind == "unknown":
            state["extra"] = True
        if kind == "orphan":
            state["audit"].append(
                {"action": "propose", "target": "absent", "outcome": "success"}
            )
        if kind == "nan":
            state["quotes"][0]["unit_cents"] = float("nan")
        with pytest.raises(ValueError):
            validate_state(state)
    with pytest.raises(ValueError, match="duplicate JSON"):
        strict_json('{"x":1,"x":2}')
    with pytest.raises(ValueError, match="nonfinite"):
        strict_json('{"x":NaN}')


def test_current_review_source_and_model_context_are_bound(tmp_path):
    store = setup(tmp_path)
    context = store.context("PR-101")
    store.update_quote("Q-203", unit_cents=1000)
    assert (
        store.propose("PR-101", "Q-201", 1400, 1, expected_context=context)["error"]
        == "recommendation_source_changed"
    )
    store.update_quote("Q-203", unit_cents=1500)
    assert store.propose("PR-101", "Q-201", 1400, 1)["ok"]
    store.update_quote("Q-203", unit_cents=1450)
    assert (
        store.decide("PROP-1", "Ava Chen", "approved")["error"]
        == "reviewed_source_changed"
    )


def test_strict_role_recommendation_and_three_role_handoff(tmp_path):
    store = setup(tmp_path)
    value = {
        "request_id": "PR-101",
        "quote_id": "Q-201",
        "expected_unit_cents": 1400,
        "expected_quote_revision": 1,
        "reason": "Lowest eligible",
    }
    good = Recommendation.model_validate(value)
    assert (
        handoff(
            store.context("PR-101"),
            {role: good for role in ("buyer", "checker", "drafter")},
        )
        == good
    )
    with pytest.raises(ValueError):
        Recommendation.model_validate({**value, "reason": " "})
    with pytest.raises(ValueError):
        Recommendation.model_validate({**value, "expected_quote_revision": True})
    with pytest.raises(ValueError):
        Recommendation.model_validate({**value, "approval": "approved"})
    review = Recommendation.model_validate(
        {
            **value,
            "outcome": "needs_review",
            "quote_id": None,
            "expected_unit_cents": None,
            "expected_quote_revision": None,
        }
    )
    with pytest.raises(ValueError, match="handoff"):
        handoff(
            store.context("PR-101"), {"buyer": good, "checker": review, "drafter": good}
        )


def _commit(file):
    return Store(file).commit("PROP-1")


def test_independent_processes_commit_an_approved_order_only_once(tmp_path):
    store = setup(tmp_path)
    approved(store)
    with ProcessPoolExecutor(
        max_workers=2, mp_context=multiprocessing.get_context("spawn")
    ) as executor:
        futures = [executor.submit(_commit, str(store.path)) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert sum(r["ok"] for r in results) == 1
    assert len(store.read()["orders"]) == 1
    assert store.read()["quotes"][0]["stock"] == 10
