"""Own native CrewAI work through final source check and proposal-only commit."""

import asyncio
import math
from contextlib import AsyncExitStack
from .bridge import NativeBridge, NativeAbort
from .crew import ROLES, build_crew, parse_recommendation, handoff, grade_recommendation
from .evidence import Receipt
from .native import create_role, from_environment


async def run_team_async(
    store,
    request_id,
    *,
    configs=None,
    output_root=None,
    timeout_seconds=120,
    writer=None,
    transports=None,
    model=None,
    base_url=None,
):
    if (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or not 0 < timeout_seconds <= 300
    ):
        raise ValueError("purchase timeout must be finite and in (0,300]")
    receipt = Receipt(store, request_id, output_root, timeout_seconds, writer)
    bridge = NativeBridge(receipt)
    stack = AsyncExitStack()
    close_attempted = False
    failure = None
    result = None
    outputs = {}
    boundaries = {}

    def callback(role):
        def record(output):
            try:
                receipt.guard()
                receipt.source_guard()
                original = str(output.raw)
                item = {
                    "role": role,
                    "raw": original,
                    "raw_sha256": receipt.sha(original.encode()),
                    "parsed": None,
                }
                receipt.data["role_outputs"].append(item)
                receipt.save()
                boundary = boundaries[role]
                if (
                    boundary.observation is None
                    or boundary.observation["status"] != "accepted"
                ):
                    raise ValueError("role lacks original native completion authority")
                parsed = parse_recommendation(original)
                error = grade_recommendation(receipt.context, parsed)
                if error:
                    raise ValueError(f"invalid {role} recommendation: {error}")
                outputs[role] = parsed
                item["parsed"] = parsed.model_dump()
                receipt.save()
                receipt.guard()
            except BaseException as error:
                receipt.failure = receipt.failure or error
                raise NativeAbort(error) from error

        return record

    try:
        receipt.save()
        receipt.guard()
        receipt.source_guard()
        configs = (
            list(configs) if configs is not None else from_environment(model, base_url)
        )
        if len(configs) != 3 or [c.role for c in configs] != list(ROLES):
            raise ValueError("buyer/checker/drafter configs must be in role order")
        for config in configs:
            config.validate()
        receipt.data["roles"] = [c.public() for c in configs]
        receipt.save()
        llms = {}
        for config in configs:
            llms[config.role], boundaries[config.role] = await create_role(
                config, receipt, stack, (transports or {}).get(config.role), bridge
            )
        crew = build_crew(
            receipt.context,
            llms=llms,
            callbacks={r: callback(r) for r in ROLES},
            task_store_path=receipt.path.parent / "crew-tasks.db",
        )
        async with asyncio.timeout_at(receipt.deadline):
            completed = await crew.akickoff()
        receipt.guard()
        receipt.source_guard()
        if len(completed.tasks_output) != 3:
            raise ValueError("all native task outputs are required")
        receipt.data["crew_usage"] = (
            completed.token_usage.model_dump(mode="json")
            if completed.token_usage is not None
            else None
        )
        recommendation = handoff(receipt.context, outputs)
        await bridge.close()
        receipt.data["native_bridge_closed"] = True
        close_attempted = True
        await stack.aclose()
        receipt.data["native_clients_closed"] = True
        receipt.save()
        receipt.guard()
        receipt.source_guard()
        receipt.data["status"] = "proposal_commit_prepared"
        receipt.data["recommendation"] = recommendation.model_dump()
        receipt.save()
        receipt.guard()
        receipt.source_guard()
        if recommendation.outcome == "needs_review":
            result = {"ok": False, "error": "needs_review"}
        else:
            result = store.propose(
                request_id,
                recommendation.quote_id,
                recommendation.expected_unit_cents,
                recommendation.expected_quote_revision,
                expected_context=receipt.context,
            )
        result.update(
            recommendation=recommendation.model_dump(),
            execution_kind="native_crewai_three_role",
            receipt_path=str(receipt.path),
            run_id=receipt.data["run_id"],
        )
    except BaseException as error:
        failure = receipt.failure or (
            error.original if isinstance(error, NativeAbort) else error
        )
        receipt.stopping = True
    finally:
        try:
            await bridge.close()
            receipt.data["native_bridge_closed"] = True
        except BaseException as error:
            failure = failure or error
            receipt.data["native_bridge_closed"] = False
            receipt.data["bridge_error"] = {
                "name": type(error).__name__,
                "message": str(error),
            }
        if not close_attempted:
            try:
                close_attempted = True
                await stack.aclose()
                receipt.data["native_clients_closed"] = True
            except BaseException as error:
                failure = failure or error
                receipt.data["close_error"] = {
                    "name": type(error).__name__,
                    "message": str(error),
                }
        try:
            receipt.finish(result, failure)
        except BaseException as error:
            failure = failure or error
            receipt.data.update(
                status="failed",
                result=None,
                durable_status="failed",
                recording_error={"name": type(error).__name__, "message": str(error)},
            )
            try:
                receipt.save(final=True)
            except BaseException:
                receipt.data["durable_status"] = "unknown"
    if failure is not None:
        failure.receipt_path = str(receipt.path)
        failure.evidence = receipt.data
        raise failure
    return result
