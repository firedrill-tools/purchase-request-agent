"""Strict procurement source and immutable reviewed-proposal contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

MAX = 2**53 - 1
Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=160, strip_whitespace=False)
]
Money = Annotated[int, Field(ge=0, le=MAX)]
Positive = Annotated[int, Field(ge=1, le=MAX)]
Currency = Literal["CAD", "USD", "EUR", "GBP"] | None


class Strict(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", revalidate_instances="always"
    )


class Request(Strict):
    id: Identifier
    sku: Identifier
    description: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    quantity: Positive
    budget_cents: Money
    currency: Currency = None
    status: Literal["open", "ordered", "cancelled"]
    revision: Positive

    @field_validator("id", "sku", "description")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("blank/control source value")
        return value


class Quote(Strict):
    id: Identifier
    supplier: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    sku: Identifier
    unit_cents: Positive
    currency: Currency = None
    stock: Money
    revision: Positive

    @field_validator("id", "sku", "supplier")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("blank/control source value")
        return value


class Context(Strict):
    request: Request
    quotes: Annotated[list[Quote], Field(max_length=512)]


class Decision(Strict):
    kind: Literal["synthetic_named_human_decision"]
    approver: Literal["Ava Chen"]
    decision: Literal["approved", "rejected"]
    proposal_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class Proposal(Strict):
    id: Identifier
    request_id: Identifier
    quote_id: Identifier
    quantity: Positive
    unit_cents: Positive
    total_cents: Positive
    request_revision: Positive
    quote_revision: Positive
    approval: Literal["pending", "approved", "rejected"]
    approver: Literal["Ava Chen"] | None
    source: Context
    content_sha256: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    decision: Decision | None = None


class Order(Strict):
    id: Identifier
    proposal_id: Identifier
    request_id: Identifier
    quote_id: Identifier
    quantity: Positive
    total_cents: Positive


class Audit(Strict):
    action: Literal["propose", "approval", "commit", "quote_update"]
    target: Identifier
    outcome: Literal["success", "approved", "rejected"]


class State(Strict):
    @model_validator(mode="before")
    @classmethod
    def exact_header(cls, value):
        if (
            not isinstance(value, dict)
            or value.get("synthetic") is not True
            or type(value.get("version")) is not int
        ):
            raise ValueError("exact synthetic/version header is required")
        return value

    synthetic: Literal[True]
    version: Literal[1]
    instance_id: str | None = None
    requests: Annotated[list[Request], Field(min_length=1, max_length=256)]
    quotes: Annotated[list[Quote], Field(min_length=1, max_length=1024)]
    proposals: Annotated[list[Proposal], Field(max_length=1024)]
    orders: Annotated[list[Order], Field(max_length=256)]
    audit: Annotated[list[Audit], Field(max_length=10000)]


class Recommendation(Strict):
    request_id: Identifier
    outcome: Literal["recommend", "needs_review"] = "recommend"
    quote_id: Identifier | None
    expected_unit_cents: Positive | None
    expected_quote_revision: Positive | None
    reason: Annotated[str, StringConstraints(min_length=1, max_length=4000)]

    @field_validator("reason", "request_id", "quote_id")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("recommendation values must not be blank")
        return value

    @model_validator(mode="after")
    def values_for_outcome(self):
        values = (self.quote_id, self.expected_unit_cents, self.expected_quote_revision)
        if self.outcome == "recommend" and any(v is None for v in values):
            raise ValueError("recommendation needs exact quote/price/revision")
        if self.outcome == "needs_review" and any(v is not None for v in values):
            raise ValueError("review cannot authorize a quote")
        return self


def canonical(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def proposal_digest(proposal) -> str:
    return digest(
        {
            k: v
            for k, v in proposal.items()
            if k not in {"approval", "approver", "decision", "content_sha256"}
        }
    )


def validate_state(value: object) -> dict:
    state = State.model_validate(value).model_dump()
    for key in ("requests", "quotes", "proposals", "orders"):
        ids = [r["id"] for r in state[key]]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate {key} identity")
    requests = {r["id"]: r for r in state["requests"]}
    quotes = {r["id"]: r for r in state["quotes"]}
    proposals = {r["id"]: r for r in state["proposals"]}
    for proposal in state["proposals"]:
        source = proposal["source"]
        request = source["request"]
        candidates = source["quotes"]
        source_ids = [q["id"] for q in candidates]
        if len(set(source_ids)) != len(source_ids) or any(
            q["sku"] != request["sku"] for q in candidates
        ):
            raise ValueError("invalid proposal quote population")
        quote = next((q for q in candidates if q["id"] == proposal["quote_id"]), None)
        if (
            request["id"] != proposal["request_id"]
            or request["id"] not in requests
            or proposal["quote_id"] not in quotes
            or request["status"] != "open"
            or quote is None
        ):
            raise ValueError("orphan/invalid proposal source")
        eligible = [
            q
            for q in candidates
            if q["currency"] == request["currency"]
            and q["stock"] >= request["quantity"]
            and q["unit_cents"] * request["quantity"] <= request["budget_cents"]
        ]
        if (
            not eligible
            or quote["id"]
            != min(eligible, key=lambda q: (q["unit_cents"], q["id"]))["id"]
        ):
            raise ValueError("proposal source has no matching cheapest eligible quote")
        if (
            proposal["quantity"] != request["quantity"]
            or proposal["unit_cents"] != quote["unit_cents"]
            or proposal["total_cents"] != quote["unit_cents"] * request["quantity"]
            or proposal["request_revision"] != request["revision"]
            or proposal["quote_revision"] != quote["revision"]
            or proposal["content_sha256"] != proposal_digest(proposal)
        ):
            raise ValueError("proposal differs from immutable source")
        decision = proposal["decision"]
        if proposal["approval"] == "pending":
            if decision is not None or proposal["approver"] is not None:
                raise ValueError("pending proposal has a decision")
        elif (
            not decision
            or decision["decision"] != proposal["approval"]
            or decision["approver"] != proposal["approver"]
            or decision["proposal_sha256"] != proposal["content_sha256"]
        ):
            raise ValueError("decision is not bound to reviewed proposal")
    seen_requests, seen_proposals = set(), set()
    for order in state["orders"]:
        p = proposals.get(order["proposal_id"])
        if (
            not p
            or p["approval"] != "approved"
            or any(
                order[k] != p[k]
                for k in ("request_id", "quote_id", "quantity", "total_cents")
            )
        ):
            raise ValueError("order differs from approved proposal")
        request = requests[order["request_id"]]
        if (
            request["status"] != "ordered"
            or request["revision"] <= p["request_revision"]
        ):
            raise ValueError("order has no completed request")
        if (
            order["request_id"] in seen_requests
            or order["proposal_id"] in seen_proposals
        ):
            raise ValueError("duplicate request/proposal order")
        seen_requests.add(order["request_id"])
        seen_proposals.add(order["proposal_id"])
    if any(
        r["status"] == "ordered" and r["id"] not in seen_requests
        for r in state["requests"]
    ):
        raise ValueError("ordered request has no order")
    if len({p["request_id"] for p in state["proposals"]}) != len(state["proposals"]):
        raise ValueError("duplicate request proposal")
    for audit in state["audit"]:
        targets = (
            quotes
            if audit["action"] == "quote_update"
            else {o["id"]: o for o in state["orders"]}
            if audit["action"] == "commit"
            else proposals
        )
        if audit["target"] not in targets:
            raise ValueError("orphan audit target")
    return state
