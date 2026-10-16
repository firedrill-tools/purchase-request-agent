"""Hash-bound imported procurement sources and exclusive private workspaces."""

from __future__ import annotations
import json
import os
import uuid
from pathlib import Path
from datetime import date
from ._dataset_manifest import DATASET_SHA256
from .schema import digest, validate_state


def source_bytes(path, limit=1048576):
    path = Path(path)
    before = path.lstat()
    if path.is_symlink() or not path.is_file() or before.st_size > limit:
        raise ValueError("dataset source must be a bounded regular file")
    raw = path.read_bytes()
    after = path.lstat()
    if len(raw) > limit or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("dataset source changed during read")
    return raw


def validate_population(population):
    if not isinstance(population, dict) or set(population) != {
        "id",
        "profile",
        "request_id",
        "task",
        "state",
        "history",
        "provenance",
    }:
        raise ValueError("invalid population keys")
    p = json.loads(json.dumps(population, allow_nan=False))
    state = validate_state(p["state"])
    p["state"] = state
    if (
        not isinstance(p["id"], str)
        or not p["id"]
        or p["profile"] not in {"starter", "busy", "ambiguous", "boundary"}
        or not isinstance(p["task"], str)
        or not p["task"].strip()
        or not any(r["id"] == p["request_id"] for r in state["requests"])
    ):
        raise ValueError("invalid population identity/focus")
    if p["provenance"] != {
        "kind": "fictional_imported_procurement_snapshot",
        "prior_model_evidence": None,
        "prior_human_authentication": None,
        "prior_supplier_delivery_evidence": None,
    }:
        raise ValueError(
            "imported provenance cannot imply prior execution/authentication/delivery"
        )
    if not isinstance(p["history"], list) or len(p["history"]) != len(state["quotes"]):
        raise ValueError("history population differs")
    seen = set()
    orders = {o["id"]: o for o in state["orders"]}
    allocated = set()
    for h in p["history"]:
        if (
            set(h)
            != {
                "quote_id",
                "imported_on",
                "opening_stock",
                "opening_revision",
                "allocations",
            }
            or h["quote_id"] in seen
        ):
            raise ValueError("invalid quote history identity")
        seen.add(h["quote_id"])
        q = next((q for q in state["quotes"] if q["id"] == h["quote_id"]), None)
        if (
            not q
            or type(h["opening_stock"]) is not int
            or not 0 <= h["opening_stock"] <= 2**53 - 1
            or type(h["opening_revision"]) is not int
            or not 1 <= h["opening_revision"] <= 2**53 - 1
        ):
            raise ValueError("invalid opening quote authority")
        imported = date.fromisoformat(h["imported_on"])
        total = 0
        for a in h["allocations"]:
            if (
                set(a) != {"order_id", "quantity", "allocated_on"}
                or a["order_id"] in allocated
            ):
                raise ValueError("invalid allocation history identity")
            o = orders.get(a["order_id"])
            if (
                not o
                or o["quote_id"] != q["id"]
                or type(a["quantity"]) is not int
                or a["quantity"] != o["quantity"]
                or date.fromisoformat(a["allocated_on"]) > imported
            ):
                raise ValueError("allocation differs from imported order")
            allocated.add(a["order_id"])
            total += a["quantity"]
        if (
            h["opening_stock"] - total != q["stock"]
            or h["opening_revision"] + len(h["allocations"]) != q["revision"]
        ):
            raise ValueError("quote history stock/revisions do not reconcile")
    if allocated != set(orders):
        raise ValueError("order history incomplete")
    return p


def populations():
    path = Path(__file__).parent / "sample_data" / "index.json"
    if not path.exists():
        path = Path(__file__).parents[2] / "data" / "index.json"
    raw = source_bytes(path)
    import hashlib

    if hashlib.sha256(raw).hexdigest() != DATASET_SHA256:
        raise ValueError("dataset authority hash differs")
    from .store import strict_json

    value = strict_json(raw.decode("utf-8"))
    if (
        value.get("id") != "purchase-populations"
        or value.get("version") != "1.0.0"
        or not isinstance(value.get("cases"), list)
        or len(value["cases"]) != 12
    ):
        raise ValueError("invalid dataset index")
    rows = [validate_population(p) for p in value["cases"]]
    if len({p["id"] for p in rows}) != 12:
        raise ValueError("duplicate population identity")
    return rows


def authority(file, state):
    dataset = state.get("dataset")
    if not dataset:
        return None
    from .store import strict_json

    meta = strict_json(source_bytes(str(file) + ".source.json", 32768).decode("utf-8"))
    if (
        not isinstance(meta, dict)
        or set(meta)
        != {
            "schema_version",
            "instance_id",
            "dataset_sha256",
            "case_id",
            "seed_state_sha256",
            "scope",
        }
        or type(meta["schema_version"]) is not int
        or meta["schema_version"] != 1
        or meta["scope"] != "original_imported_seed_snapshot"
    ):
        raise ValueError("invalid source metadata")
    p = next((p for p in populations() if p["id"] == meta["case_id"]), None)
    if (
        not p
        or meta["instance_id"] != state["instance_id"]
        or meta["dataset_sha256"] != DATASET_SHA256
        or dataset["source_sha256"] != DATASET_SHA256
        or dataset["case_id"] != p["id"]
        or meta["seed_state_sha256"] != digest(p["state"])
    ):
        raise ValueError("workspace/source identity differs")
    return meta, p


def exclusive(file, value):
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def initialize(file, case_id="original-quotes"):
    file = Path(file).absolute()
    p = next((p for p in populations() if p["id"] == case_id), None)
    if p is None:
        raise ValueError("unknown dataset case")
    meta_file = Path(str(file) + ".source.json")
    mutex = file.with_name("." + file.name + ".purchase-lock.sqlite")
    if any(path.exists() or path.is_symlink() for path in (file, meta_file, mutex)):
        raise FileExistsError("workspace destination exists")
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = json.loads(json.dumps(p["state"]))
    state["instance_id"] = str(uuid.uuid4())
    state["dataset"] = {
        "id": "purchase-populations",
        "version": "1.0.0",
        "case_id": case_id,
        "source_sha256": DATASET_SHA256,
    }
    state = validate_state(state)
    meta = {
        "schema_version": 1,
        "instance_id": state["instance_id"],
        "dataset_sha256": DATASET_SHA256,
        "case_id": case_id,
        "seed_state_sha256": digest(p["state"]),
        "scope": "original_imported_seed_snapshot",
    }
    created = False
    try:
        exclusive(meta_file, meta)
        created = True
        exclusive(file, state)
    except BaseException:
        if created:
            meta_file.unlink(missing_ok=True)
        raise
    fd = os.open(file.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return {
        "state": str(file),
        "case_id": case_id,
        "instance_id": state["instance_id"],
        "request_id": p["request_id"],
        "task": p["task"],
        "requests": len(state["requests"]),
        "quotes": len(state["quotes"]),
    }


def workspace_info(file, state):
    bound = authority(file, state)
    current = json.loads(json.dumps(state))
    current["instance_id"] = None
    current["dataset"] = None
    return {
        "state": str(Path(file).absolute()),
        "instance_id": state["instance_id"],
        "case_id": bound[1]["id"] if bound else None,
        "scope": bound[0]["scope"] if bound else "caller_supplied_legacy_source",
        "original_seed_match": digest(current) == bound[0]["seed_state_sha256"]
        if bound
        else None,
        "current_state_sha256": digest(state),
        "imported_history": bound[1]["history"] if bound else None,
        "prior_model_evidence": None,
        "prior_human_authentication": None,
        "prior_supplier_delivery_evidence": None,
    }


def reset(file, destination, state):
    bound = authority(file, state)
    if not bound:
        raise ValueError("reset requires a hash-bound dataset workspace")
    if Path(file).absolute() == Path(destination).absolute():
        raise ValueError("reset needs a fresh destination")
    return initialize(destination, bound[1]["id"])
