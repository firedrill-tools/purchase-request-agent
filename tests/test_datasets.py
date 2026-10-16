import asyncio
import copy
import json
from pathlib import Path
import pytest

from purchase_request.datasets import (
    populations,
    initialize,
    workspace_info,
    reset,
    validate_population,
)
from purchase_request.store import Store
from purchase_request.crew import ROLES
from purchase_request.worker import run_team_async
from purchase_request.native import RoleConfig
from test_native import Provider, body


@pytest.mark.parametrize("population", populations(), ids=lambda p: p["id"])
def test_private_population_initialization_and_reset_preserve_current_source(
    tmp_path, population
):
    file = tmp_path / "state.json"
    created = initialize(file, population["id"])
    store = Store(file)
    assert workspace_info(file, store.read())["original_seed_match"]
    assert (
        file.stat().st_mode & 0o777 == 0o600
        and Path(str(file) + ".source.json").stat().st_mode & 0o777 == 0o600
    )
    before = file.read_bytes()
    metadata = Path(str(file) + ".source.json").read_bytes()
    with pytest.raises(FileExistsError):
        initialize(file, population["id"])
    fresh = reset(file, tmp_path / "fresh.json", store.read())
    assert fresh["instance_id"] != created["instance_id"]
    assert (
        file.read_bytes() == before
        and Path(str(file) + ".source.json").read_bytes() == metadata
    )
    assert workspace_info(
        tmp_path / "fresh.json", Store(tmp_path / "fresh.json").read()
    )["original_seed_match"]


@pytest.mark.parametrize("population", populations(), ids=lambda p: p["id"])
def test_actual_native_roles_and_separate_human_decision_for_every_population(
    tmp_path, population
):
    async def check():
        file = tmp_path / "state.json"
        initialize(file, population["id"])
        store = Store(file)
        context = store.context(population["request_id"])
        request = context["request"]
        eligible = [
            q
            for q in context["quotes"]
            if q["currency"] == request["currency"]
            and q["stock"] >= request["quantity"]
            and q["unit_cents"] * request["quantity"] <= request["budget_cents"]
        ]
        selected = (
            min(eligible, key=lambda q: (q["unit_cents"], q["id"]))
            if eligible
            else None
        )
        rec = {
            "request_id": request["id"],
            "outcome": "recommend" if selected else "needs_review",
            "quote_id": selected["id"] if selected else None,
            "expected_unit_cents": selected["unit_cents"] if selected else None,
            "expected_quote_revision": selected["revision"] if selected else None,
            "reason": "Compare supplied source; leave human decision separate.",
        }
        clients = {
            role: Provider(role, response=body(role, value=rec)) for role in ROLES
        }
        configs = [
            RoleConfig(
                role,
                "controlled",
                "synthetic-native-key",
                "https://controlled.example/v1",
            )
            for role in ROLES
        ]
        before = store.read()
        result = await run_team_async(
            store, request["id"], configs=configs, transports=clients
        )
        state = store.read()
        assert len(state["orders"]) == len(before["orders"]) and len(
            state["proposals"]
        ) == len(before["proposals"]) + (1 if selected else 0)
        assert all(len(p.requests) == 1 and p.closed for p in clients.values())
        prompts = "".join(p.requests[0].content.decode() for p in clients.values())
        assert (
            "purchase-populations" not in prompts
            and "fictional_imported_procurement_snapshot" not in prompts
        )
        if selected:
            proposal = result["proposal"]
            assert proposal["approval"] == "pending" and proposal["decision"] is None
            assert store.decide(proposal["id"], "Ava Chen", "approved")["ok"]
            order = store.commit(proposal["id"])
            assert order["ok"]
            assert (
                order["order"]["total_cents"]
                == selected["unit_cents"] * request["quantity"]
            )
            assert len(store.read()["orders"]) == len(before["orders"]) + 1
        else:
            assert result["error"] == "needs_review"

    asyncio.run(check())


def test_coherent_busy_history_and_invalid_imports_are_refused():
    busy = next(p for p in populations() if p["profile"] == "busy")
    assert (
        len(busy["state"]["requests"]) == 20
        and len(busy["state"]["quotes"]) == 80
        and len(busy["state"]["orders"]) == 4
    )
    assert busy["provenance"]["prior_human_authentication"] is None
    for kind in ("stock", "revision", "chronology", "duplicate", "missing"):
        p = copy.deepcopy(busy)
        if kind == "stock":
            p["history"][0]["opening_stock"] += 1
        if kind == "revision":
            p["history"][0]["opening_revision"] += 1
        if kind == "chronology":
            next(h for h in p["history"] if h["allocations"])["allocations"][0][
                "allocated_on"
            ] = "2026-11-01"
        if kind == "duplicate":
            p["history"][1]["quote_id"] = p["history"][0]["quote_id"]
        if kind == "missing":
            next(h for h in p["history"] if h["allocations"])["allocations"] = []
        with pytest.raises(ValueError):
            validate_population(p)


def test_valid_human_edits_and_missing_source_metadata_are_never_repaired(tmp_path):
    file = tmp_path / "state.json"
    initialize(file)
    store = Store(file)
    store.update_quote("Q-203", unit_cents=1450)
    before = file.read_bytes()
    assert not workspace_info(file, store.read())["original_seed_match"]
    reset(file, tmp_path / "fresh.json", store.read())
    assert file.read_bytes() == before
    Path(str(file) + ".source.json").unlink()
    with pytest.raises(FileNotFoundError):
        store.read()
    assert file.read_bytes() == before


def test_foreign_source_metadata_and_new_instance_fence_existing_owners(tmp_path):
    file = tmp_path / "a.json"
    other = tmp_path / "b.json"
    initialize(file)
    initialize(other)
    store = Store(file)
    Path(str(file) + ".source.json").write_bytes(
        Path(str(other) + ".source.json").read_bytes()
    )
    with pytest.raises(ValueError, match="identity differs"):
        store.read()
