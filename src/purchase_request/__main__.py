"""CLI for a local purchase request and explicit synthetic approval."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .crew import run_team
from .store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="CrewAI purchase request example over synthetic local data")
    parser.add_argument("command", choices=["init", "context", "recommend", "decide", "commit", "update-quote"])
    parser.add_argument("--state", required=True)
    parser.add_argument("--request", default="PR-101")
    parser.add_argument("--proposal")
    parser.add_argument("--quote")
    parser.add_argument("--unit-cents", type=int)
    parser.add_argument("--stock", type=int)
    parser.add_argument("--approver")
    parser.add_argument("--decision", choices=["approved", "rejected"])
    parser.add_argument("--allow-writes", action="store_true")
    args = parser.parse_args()
    state_path = Path(args.state)
    if args.command == "init":
        if state_path.exists():
            parser.error("state already exists")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).parents[2] / "fixtures" / "starter.json", state_path)
        print(json.dumps({"state": str(state_path), "request_count": len(Store(state_path).read()["requests"])}))
        return
    store = Store(state_path)
    if args.command == "context":
        result = store.context(args.request)
    elif args.command == "recommend":
        if not args.allow_writes:
            parser.error("recommend requires --allow-writes to record a proposal")
        result = run_team(store, args.request)
    elif args.command == "decide":
        if not args.proposal or not args.approver or not args.decision:
            parser.error("decide requires --proposal, --approver and --decision")
        result = store.decide(args.proposal, args.approver, args.decision)
    elif args.command == "commit":
        if not args.proposal or not args.allow_writes:
            parser.error("commit requires --proposal and --allow-writes")
        result = store.commit(args.proposal)
    else:
        if not args.quote or (args.unit_cents is None and args.stock is None):
            parser.error("update-quote requires --quote and --unit-cents or --stock")
        result = store.update_quote(args.quote, unit_cents=args.unit_cents, stock=args.stock)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
