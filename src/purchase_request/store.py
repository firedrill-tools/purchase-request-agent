"""Synthetic procurement transactions and exact reviewed-source approval."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from .schema import canonical, digest, proposal_digest, validate_state, Positive, Money
from pydantic import TypeAdapter


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def invalid(value):
        raise ValueError(f"nonfinite JSON value: {value}")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def _source(path):
    initial = path.lstat()
    if path.is_symlink() or not path.is_file() or initial.st_size > 8 * 1024 * 1024:
        raise ValueError("state must be a bounded regular file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        with os.fdopen(os.dup(fd), "rb") as stream:
            raw = stream.read(8 * 1024 * 1024 + 1)
        after, current = os.fstat(fd), path.lstat()
        if (
            len(raw) > 8 * 1024 * 1024
            or (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino)
            or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)
            or (before.st_size, before.st_mtime_ns)
            != (after.st_size, after.st_mtime_ns)
            or before.st_size != len(raw)
        ):
            raise ValueError("state changed during read")
        return (
            validate_state(strict_json(raw.decode("utf-8"))),
            raw,
            (before.st_dev, before.st_ino),
        )
    finally:
        os.close(fd)


def _context(state, request_id):
    request = next((r for r in state["requests"] if r["id"] == request_id), None)
    if request is None:
        raise ValueError("request_not_found")
    return {
        "request": request,
        "quotes": sorted(
            [q for q in state["quotes"] if q["sku"] == request["sku"]],
            key=lambda q: q["id"],
        ),
    }


def _check(context, quote_id):
    request = context["request"]
    quote = next((q for q in context["quotes"] if q["id"] == quote_id), None)
    if request["status"] != "open":
        return "request_not_open"
    if quote is None:
        return "wrong_sku"
    if quote["currency"] != request["currency"]:
        return "currency_mismatch"
    if quote["stock"] < request["quantity"]:
        return "insufficient_stock"
    if quote["unit_cents"] * request["quantity"] > request["budget_cents"]:
        return "over_budget"
    eligible = [
        q
        for q in context["quotes"]
        if q["currency"] == request["currency"]
        and q["stock"] >= request["quantity"]
        and q["unit_cents"] * request["quantity"] <= request["budget_cents"]
    ]
    if quote_id != min(eligible, key=lambda q: (q["unit_cents"], q["id"]))["id"]:
        return "better_eligible_quote_exists"
    return None


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).absolute()
        state = self.read()
        self.path = self.path.resolve()
        self.instance = state["instance_id"]
        self.lock_path = self.path.with_name(f".{self.path.name}.purchase-lock.sqlite")
        self._thread_lock = threading.RLock()
        self._prior = None

    def read(self) -> dict:
        state, _, _ = _source(self.path)
        if hasattr(self, "instance") and state["instance_id"] != self.instance:
            raise ValueError("workspace_instance_changed")
        return state

    def snapshot(self):
        state, raw, identity = _source(self.path)
        if state["instance_id"] != self.instance:
            raise ValueError("workspace_instance_changed")
        return state, raw, identity

    @contextmanager
    def transaction(self):
        with self._thread_lock:
            try:
                fd = os.open(
                    self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
                os.close(fd)
            except FileExistsError:
                if self.lock_path.is_symlink() or not self.lock_path.is_file():
                    raise ValueError("invalid persistent procurement mutex")
            db = sqlite3.connect(self.lock_path, timeout=5)
            try:
                db.execute("BEGIN IMMEDIATE")
                self._prior = self.snapshot()
                yield self._prior[0]
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                self._prior = None
                db.close()

    def _write(self, state: dict) -> None:
        if self._prior is None:
            raise RuntimeError("state write requires an owned transaction")
        state = validate_state(state)
        raw = (
            json.dumps(state, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        ).encode()
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("state size limit")
        fd, temporary = tempfile.mkstemp(prefix=".purchase-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            current = self.snapshot()
            if current[1:] != self._prior[1:]:
                raise ValueError("state_changed_before_commit")
            os.replace(temporary, self.path)
            parent = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def context(self, request_id: str) -> dict:
        return _context(self.read(), request_id)

    def propose(
        self,
        request_id,
        quote_id,
        expected_unit_cents,
        expected_quote_revision,
        *,
        expected_context=None,
    ):
        try:
            TypeAdapter(Positive).validate_python(expected_unit_cents, strict=True)
            TypeAdapter(Positive).validate_python(expected_quote_revision, strict=True)
        except ValueError:
            return {"ok": False, "error": "invalid_price_or_revision"}
        with self.transaction() as state:
            request = next(
                (r for r in state["requests"] if r["id"] == request_id), None
            )
            quote = next((q for q in state["quotes"] if q["id"] == quote_id), None)
            if request is None or quote is None:
                return {"ok": False, "error": "request_or_quote_not_found"}
            if request["status"] != "open":
                return {"ok": False, "error": "request_not_open"}
            if any(p["request_id"] == request_id for p in state["proposals"]):
                return {"ok": False, "error": "proposal_already_exists"}
            context = _context(state, request_id)
            if expected_context is not None and canonical(context) != canonical(
                expected_context
            ):
                return {"ok": False, "error": "recommendation_source_changed"}
            if (
                quote["revision"] != expected_quote_revision
                or quote["unit_cents"] != expected_unit_cents
            ):
                return {"ok": False, "error": "stale_quote"}
            block = _check(context, quote_id)
            if block:
                return {"ok": False, "error": block}
            ids = {p["id"] for p in state["proposals"]}
            n = 1
            while f"PROP-{n}" in ids:
                n += 1
            proposal = {
                "id": f"PROP-{n}",
                "request_id": request_id,
                "quote_id": quote_id,
                "quantity": request["quantity"],
                "unit_cents": quote["unit_cents"],
                "total_cents": quote["unit_cents"] * request["quantity"],
                "request_revision": request["revision"],
                "quote_revision": quote["revision"],
                "approval": "pending",
                "approver": None,
                "source": context,
                "decision": None,
            }
            proposal["content_sha256"] = proposal_digest(proposal)
            state["proposals"].append(proposal)
            state["audit"].append(
                {"action": "propose", "target": proposal["id"], "outcome": "success"}
            )
            self._write(state)
            return {"ok": True, "proposal": proposal}

    def decide(self, proposal_id, approver, decision):
        with self.transaction() as state:
            proposal = next(
                (p for p in state["proposals"] if p["id"] == proposal_id), None
            )
            if proposal is None:
                return {"ok": False, "error": "proposal_not_found"}
            if approver != "Ava Chen":
                return {"ok": False, "error": "approver_not_authorized"}
            if (
                decision not in {"approved", "rejected"}
                or proposal["approval"] != "pending"
            ):
                return {"ok": False, "error": "invalid_decision"}
            if canonical(_context(state, proposal["request_id"])) != canonical(
                proposal["source"]
            ):
                return {"ok": False, "error": "reviewed_source_changed"}
            proposal["approval"] = decision
            proposal["approver"] = approver
            proposal["decision"] = {
                "kind": "synthetic_named_human_decision",
                "approver": approver,
                "decision": decision,
                "proposal_sha256": proposal["content_sha256"],
            }
            state["audit"].append(
                {"action": "approval", "target": proposal_id, "outcome": decision}
            )
            self._write(state)
            return {"ok": True, "proposal": proposal}

    def commit(self, proposal_id):
        with self.transaction() as state:
            proposal = next(
                (p for p in state["proposals"] if p["id"] == proposal_id), None
            )
            if proposal is None:
                return {"ok": False, "error": "proposal_not_found"}
            if proposal["approval"] != "approved":
                return {"ok": False, "error": "approval_required"}
            if any(o["proposal_id"] == proposal_id for o in state["orders"]):
                return {"ok": False, "error": "already_ordered"}
            request = next(
                r for r in state["requests"] if r["id"] == proposal["request_id"]
            )
            quote = next(q for q in state["quotes"] if q["id"] == proposal["quote_id"])
            if request != proposal["source"]["request"]:
                return {"ok": False, "error": "request_changed"}
            if quote["stock"] < proposal["quantity"]:
                return {"ok": False, "error": "stock_changed"}
            original_quote = next(
                q for q in proposal["source"]["quotes"] if q["id"] == quote["id"]
            )
            if quote != original_quote:
                return {"ok": False, "error": "quote_changed"}
            if canonical(_context(state, request["id"])) != canonical(
                proposal["source"]
            ):
                return {"ok": False, "error": "quote_population_changed"}
            block = _check(_context(state, request["id"]), quote["id"])
            if block:
                return {"ok": False, "error": block}
            if request["revision"] == 2**53 - 1 or quote["revision"] == 2**53 - 1:
                return {"ok": False, "error": "revision_exhausted"}
            quote["stock"] -= proposal["quantity"]
            quote["revision"] += 1
            request["status"] = "ordered"
            request["revision"] += 1
            ids = {o["id"] for o in state["orders"]}
            n = 1
            while f"PO-{n}" in ids:
                n += 1
            order = {
                "id": f"PO-{n}",
                "proposal_id": proposal_id,
                "request_id": request["id"],
                "quote_id": quote["id"],
                "quantity": proposal["quantity"],
                "total_cents": proposal["total_cents"],
            }
            state["orders"].append(order)
            state["audit"].append(
                {"action": "commit", "target": order["id"], "outcome": "success"}
            )
            self._write(state)
            return {"ok": True, "order": order}

    def update_quote(self, quote_id, *, unit_cents=None, stock=None):
        with self.transaction() as state:
            quote = next((q for q in state["quotes"] if q["id"] == quote_id), None)
            if quote is None:
                return {"ok": False, "error": "quote_not_found"}
            if unit_cents is None and stock is None:
                return {"ok": False, "error": "no_quote_change"}
            for value, adapter, error in (
                (unit_cents, Positive, "invalid_price"),
                (stock, Money, "invalid_stock"),
            ):
                if value is not None:
                    try:
                        TypeAdapter(adapter).validate_python(value, strict=True)
                    except ValueError:
                        return {"ok": False, "error": error}
            if quote["revision"] == 2**53 - 1:
                return {"ok": False, "error": "revision_exhausted"}
            if unit_cents is not None:
                quote["unit_cents"] = unit_cents
            if stock is not None:
                quote["stock"] = stock
            quote["revision"] += 1
            state["audit"].append(
                {"action": "quote_update", "target": quote_id, "outcome": "success"}
            )
            self._write(state)
            return {"ok": True, "quote": quote}
