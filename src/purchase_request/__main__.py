"""Synthetic procurement CLI; recommendation never owns approval/order tools."""

from __future__ import annotations
import argparse
import sys
from contextlib import redirect_stdout
import asyncio
import json
import os
import signal
import uuid
from pathlib import Path
from .store import Store


async def recommend(store, args):
    from .worker import run_team_async

    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    received = []
    listeners = []

    def cancel(name):
        if not received:
            received.append(name)
            task.cancel()

    for name in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            loop.add_signal_handler(name, cancel, name)
            listeners.append(name)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        result = await run_team_async(
            store,
            args.request,
            model=args.model,
            base_url=args.base_url,
            timeout_seconds=args.timeout_seconds,
            output_root=args.output_root,
        )
        return result, 0 if result["ok"] else 2
    except BaseException as error:
        evidence = getattr(error, "evidence", None)
        return {
            "ok": False,
            "error": type(error).__name__,
            "message": str(error),
            "receipt_path": getattr(error, "receipt_path", None),
            "native_clients_closed": evidence.get("native_clients_closed", False)
            if evidence
            else False,
            "native_bridge_closed": evidence.get("native_bridge_closed", False)
            if evidence
            else False,
            "cost_usd": None,
        }, 128 + received[0] if received else 1
    finally:
        for name in listeners:
            loop.remove_signal_handler(name)


def cli() -> int:
    parser = argparse.ArgumentParser(
        description="CrewAI purchase request example over local synthetic state"
    )
    parser.add_argument(
        "command",
        choices=["init", "context", "recommend", "decide", "commit", "update-quote"],
    )
    parser.add_argument("--state", required=True)
    parser.add_argument("--request", default="PR-101")
    parser.add_argument("--proposal")
    parser.add_argument("--quote")
    parser.add_argument("--unit-cents", type=int)
    parser.add_argument("--stock", type=int)
    parser.add_argument("--approver")
    parser.add_argument("--decision", choices=["approved", "rejected"])
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--max-provider-requests", type=int)
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--output-root")
    args = parser.parse_args()
    state_path = Path(args.state)
    if args.command == "init":
        source = Path(__file__).parents[2] / "fixtures" / "starter.json"
        if not source.exists():
            source = Path(__file__).parent / "original_data" / "starter.json"
        state = Store(source).read()
        state["instance_id"] = str(uuid.uuid4())
        state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(state_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(state, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            parser.error("state already exists")
        print(
            json.dumps(
                {
                    "state": str(state_path),
                    "instance_id": state["instance_id"],
                    "requests": len(state["requests"]),
                }
            )
        )
        return 0
    store = Store(state_path)
    if args.command == "context":
        result = store.context(args.request)
    elif args.command == "recommend":
        if not args.live:
            print(
                json.dumps(
                    {
                        "status": "plan",
                        "request_id": args.request,
                        "model": args.model or os.getenv("PURCHASE_MODEL"),
                        "maximum_prepared_provider_requests": 3,
                        "allow_writes": args.allow_writes,
                        "source_changed": False,
                        "cost_usd": None,
                    }
                )
            )
            return 0
        if not args.allow_writes:
            parser.error(
                "recommend --live requires --allow-writes for a synthetic proposal"
            )
        if args.max_provider_requests is None or args.max_provider_requests < 3:
            parser.error("--max-provider-requests must cover all3 role requests")
        with redirect_stdout(sys.stderr):
            result, code = asyncio.run(recommend(store, args))
        print(json.dumps(result, indent=2))
        return code
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
        result = store.update_quote(
            args.quote, unit_cents=args.unit_cents, stock=args.stock
        )
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok", True) else 2


def main():
    try:
        code = cli()
    except (ValueError, OSError) as error:
        print(
            json.dumps(
                {"ok": False, "error": type(error).__name__, "message": str(error)}
            )
        )
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
