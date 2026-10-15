import json
from pathlib import Path

from crewai import BaseLLM

from purchase_request.crew import build_crew, run_team
from purchase_request.store import Store


FIXTURE = Path(__file__).parent.parent / "fixtures" / "starter.json"


def setup(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(FIXTURE.read_text())
    return Store(state)


def propose(store):
    return store.propose("PR-101", "Q-201", 1400, 1)


def test_cheapest_eligible_quote_and_approval_gate(tmp_path):
    store = setup(tmp_path)
    assert store.propose("PR-101", "Q-202", 1200, 1)["error"] == "insufficient_stock"
    assert store.propose("PR-101", "Q-204", 2500, 1)["error"] == "over_budget"
    assert store.propose("PR-101", "Q-203", 1500, 1)["error"] == "better_eligible_quote_exists"
    result = propose(store)
    assert result["ok"] and result["proposal"]["total_cents"] == 28000
    assert store.commit("PROP-1")["error"] == "approval_required"
    assert store.decide("PROP-1", "Agent", "approved")["error"] == "approver_not_authorized"
    assert store.decide("PROP-1", "Ava Chen", "approved")["ok"]
    assert store.commit("PROP-1")["order"]["id"] == "PO-1"
    assert store.commit("PROP-1")["error"] == "already_ordered"
    assert store.read()["quotes"][0]["stock"] == 10


def test_changed_price_and_stock_block_after_approval(tmp_path):
    store = setup(tmp_path)
    propose(store)
    store.decide("PROP-1", "Ava Chen", "approved")
    store.update_quote("Q-201", unit_cents=1600)
    assert store.commit("PROP-1")["error"] == "quote_changed"
    assert store.read()["orders"] == []

def test_stock_change_blocks_and_rejection_sticks(tmp_path):
    stock_dir = tmp_path / "stock"
    stock_dir.mkdir()
    store = setup(stock_dir)
    propose(store)
    store.decide("PROP-1", "Ava Chen", "approved")
    store.update_quote("Q-201", stock=5)
    assert store.commit("PROP-1")["error"] == "stock_changed"
    assert store.read()["orders"] == []

    reject_dir = tmp_path / "reject"
    reject_dir.mkdir()
    store = setup(reject_dir)
    propose(store)
    store.decide("PROP-1", "Ava Chen", "rejected")
    assert store.commit("PROP-1")["error"] == "approval_required"
    assert store.read()["orders"] == []


class StubLLM(BaseLLM):
    def __init__(self):
        super().__init__(model="synthetic-purchase-stub", temperature=0)
        self.calls = []

    def call(self, messages, tools=None, callbacks=None, available_functions=None, **kwargs):
        self.calls.append(messages)
        return json.dumps({"request_id": "PR-101", "quote_id": "Q-201", "expected_unit_cents": 1400,
                           "expected_quote_revision": 1, "reason": "Lowest eligible quote"})

    def supports_function_calling(self):
        return False


def test_actual_three_role_crew_creates_proposal_not_order(tmp_path):
    store = setup(tmp_path)
    llm = StubLLM()
    crew = build_crew(store.context("PR-101"), llm)
    assert [agent.role for agent in crew.agents] == ["Supplier quote analyst", "Inventory and budget checker", "Purchase proposal drafter"]
    result = run_team(store, "PR-101", llm)
    assert result["ok"]
    assert len(llm.calls) >= 3
    assert len(store.read()["proposals"]) == 1
    assert store.read()["orders"] == []
