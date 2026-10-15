"""Exact procurement rules and approval boundary over a single local JSON workspace."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.read()

    def read(self) -> dict:
        state = json.loads(self.path.read_text())
        if state.get("synthetic") is not True or state.get("version") != 1:
            raise ValueError("only version 1 synthetic purchase state is supported")
        return state

    def _write(self, state: dict) -> None:
        fd, temp = tempfile.mkstemp(prefix=".purchase-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream, indent=2)
                stream.write("\n")
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def context(self, request_id: str) -> dict:
        state = self.read()
        request = next((item for item in state["requests"] if item["id"] == request_id), None)
        if request is None:
            raise ValueError("request_not_found")
        return {"request": request, "quotes": [q for q in state["quotes"] if q["sku"] == request["sku"]]}

    def propose(self, request_id: str, quote_id: str, expected_unit_cents: int, expected_quote_revision: int) -> dict:
        state = self.read()
        request = next((item for item in state["requests"] if item["id"] == request_id), None)
        quote = next((item for item in state["quotes"] if item["id"] == quote_id), None)
        if request is None or quote is None:
            return {"ok": False, "error": "request_or_quote_not_found"}
        if request["status"] != "open":
            return {"ok": False, "error": "request_not_open"}
        if any(p["request_id"] == request_id for p in state["proposals"]):
            return {"ok": False, "error": "proposal_already_exists"}
        if quote["sku"] != request["sku"]:
            return {"ok": False, "error": "wrong_sku"}
        if quote["revision"] != expected_quote_revision or quote["unit_cents"] != expected_unit_cents:
            return {"ok": False, "error": "stale_quote"}
        if quote["stock"] < request["quantity"]:
            return {"ok": False, "error": "insufficient_stock"}
        total = quote["unit_cents"] * request["quantity"]
        if total > request["budget_cents"]:
            return {"ok": False, "error": "over_budget"}
        eligible = [q for q in state["quotes"] if q["sku"] == request["sku"] and q["stock"] >= request["quantity"] and q["unit_cents"] * request["quantity"] <= request["budget_cents"]]
        cheapest = min(eligible, key=lambda q: (q["unit_cents"], q["id"]))
        if quote_id != cheapest["id"]:
            return {"ok": False, "error": "better_eligible_quote_exists"}
        proposal = {"id": f"PROP-{len(state['proposals']) + 1}", "request_id": request_id, "quote_id": quote_id,
                    "quantity": request["quantity"], "unit_cents": quote["unit_cents"], "total_cents": total,
                    "request_revision": request["revision"], "quote_revision": quote["revision"],
                    "approval": "pending", "approver": None}
        state["proposals"].append(proposal)
        state["audit"].append({"action": "propose", "target": proposal["id"], "outcome": "success"})
        self._write(state)
        return {"ok": True, "proposal": proposal}

    def decide(self, proposal_id: str, approver: str, decision: str) -> dict:
        state = self.read()
        proposal = next((item for item in state["proposals"] if item["id"] == proposal_id), None)
        if proposal is None:
            return {"ok": False, "error": "proposal_not_found"}
        if approver != "Ava Chen":
            return {"ok": False, "error": "approver_not_authorized"}
        if decision not in {"approved", "rejected"} or proposal["approval"] != "pending":
            return {"ok": False, "error": "invalid_decision"}
        proposal["approval"] = decision
        proposal["approver"] = approver
        state["audit"].append({"action": "approval", "target": proposal_id, "outcome": decision})
        self._write(state)
        return {"ok": True, "proposal": proposal}

    def commit(self, proposal_id: str) -> dict:
        state = self.read()
        proposal = next((item for item in state["proposals"] if item["id"] == proposal_id), None)
        if proposal is None:
            return {"ok": False, "error": "proposal_not_found"}
        if proposal["approval"] != "approved":
            return {"ok": False, "error": "approval_required"}
        if any(order["proposal_id"] == proposal_id for order in state["orders"]):
            return {"ok": False, "error": "already_ordered"}
        request = next(item for item in state["requests"] if item["id"] == proposal["request_id"])
        quote = next(item for item in state["quotes"] if item["id"] == proposal["quote_id"])
        if request["status"] != "open" or request["revision"] != proposal["request_revision"]:
            return {"ok": False, "error": "request_changed"}
        if quote["stock"] < proposal["quantity"]:
            return {"ok": False, "error": "stock_changed"}
        if quote["revision"] != proposal["quote_revision"] or quote["unit_cents"] != proposal["unit_cents"]:
            return {"ok": False, "error": "quote_changed"}
        if quote["unit_cents"] * proposal["quantity"] != proposal["total_cents"] or proposal["total_cents"] > request["budget_cents"]:
            return {"ok": False, "error": "amount_changed"}
        quote["stock"] -= proposal["quantity"]
        quote["revision"] += 1
        request["status"] = "ordered"
        request["revision"] += 1
        order = {"id": f"PO-{len(state['orders']) + 1}", "proposal_id": proposal_id, "request_id": request["id"],
                 "quote_id": quote["id"], "quantity": proposal["quantity"], "total_cents": proposal["total_cents"]}
        state["orders"].append(order)
        state["audit"].append({"action": "commit", "target": order["id"], "outcome": "success"})
        self._write(state)
        return {"ok": True, "order": order}

    def update_quote(self, quote_id: str, *, unit_cents: int | None = None, stock: int | None = None) -> dict:
        state = self.read()
        quote = next((item for item in state["quotes"] if item["id"] == quote_id), None)
        if quote is None:
            return {"ok": False, "error": "quote_not_found"}
        if unit_cents is not None:
            if unit_cents <= 0:
                return {"ok": False, "error": "invalid_price"}
            quote["unit_cents"] = unit_cents
        if stock is not None:
            if stock < 0:
                return {"ok": False, "error": "invalid_stock"}
            quote["stock"] = stock
        quote["revision"] += 1
        state["audit"].append({"action": "quote_update", "target": quote_id, "outcome": "success"})
        self._write(state)
        return {"ok": True, "quote": quote}
