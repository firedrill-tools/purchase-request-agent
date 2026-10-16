import asyncio
import json
from pathlib import Path

import httpx
import pytest

from purchase_request.crew import ROLES
from purchase_request.native import RoleConfig
from purchase_request.worker import run_team_async

FIXTURE = Path(__file__).parent.parent / "fixtures/starter.json"
REC = {
    "request_id": "PR-101",
    "quote_id": "Q-201",
    "expected_unit_cents": 1400,
    "expected_quote_revision": 1,
    "reason": "Lowest eligible quote",
}


def body(role, *, finish="stop", value=None, usage="normal"):
    result = {
        "id": f"chatcmpl-{role}",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "controlled-purchase",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(REC if value is None else value),
                    "refusal": None,
                },
                "finish_reason": finish,
                "logprobs": None,
            }
        ],
        "unknown_original": {"role": role},
    }
    if usage != "missing":
        result["usage"] = (
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
            if usage == "normal"
            else usage
        )
    return result


class Provider(httpx.AsyncBaseTransport):
    def __init__(
        self,
        role,
        response=None,
        status=200,
        wait=None,
        hook=None,
        raw=None,
        close_error=False,
    ):
        (
            self.role,
            self.response,
            self.status,
            self.wait,
            self.hook,
            self.raw,
            self.close_error,
        ) = role, response, status, wait, hook, raw, close_error
        self.requests = []
        self.closed = False
        self.started = asyncio.Event()
        self.drained = asyncio.Event()

    async def handle_async_request(self, request):
        self.requests.append(request)
        self.started.set()
        try:
            if self.wait is not None:
                await self.wait.wait()
            if self.hook:
                self.hook()
            return (
                httpx.Response(
                    self.status, json=self.response or body(self.role), request=request
                )
                if self.raw is None
                else httpx.Response(self.status, content=self.raw, request=request)
            )
        finally:
            self.drained.set()

    async def aclose(self):
        self.closed = True
        if self.close_error:
            raise RuntimeError("controlled close failure")


def setup(tmp_path):
    from purchase_request.store import Store

    file = tmp_path / "state.json"
    file.write_bytes(FIXTURE.read_bytes())
    return Store(file)


def configs():
    return [
        RoleConfig(
            role, "controlled", "synthetic-native-key", "https://controlled.example/v1"
        )
        for role in ROLES
    ]


def transports(**changes):
    return {role: Provider(role, **changes.get(role, {})) for role in ROLES}


async def run(store, clients, **kwargs):
    return await run_team_async(
        store, "PR-101", configs=configs(), transports=clients, **kwargs
    )


def test_actual_native_three_role_flow_proposes_without_approving_or_ordering(tmp_path):
    async def check():
        store = setup(tmp_path)
        clients = transports()
        result = await run(store, clients)
        assert result["ok"] and result["proposal"]["approval"] == "pending"
        assert (
            not store.read()["orders"]
            and store.read()["proposals"][0]["decision"] is None
        )
        data = json.loads(Path(result["receipt_path"]).read_text())
        assert (
            data["native_clients_closed"]
            and data["native_bridge_closed"]
            and len(data["role_outputs"]) == 3
        )
        assert all(len(p.requests) == 1 and p.closed for p in clients.values())
        assert all(
            json.loads(p.requests[0].content)["max_completion_tokens"] == 2048
            for p in clients.values()
        )
        assert all(
            p.requests[0].headers["authorization"] == "Bearer synthetic-native-key"
            and "openai-organization" not in p.requests[0].headers
            for p in clients.values()
        )
        assert (
            data["native"][0]["response"]["unknown_original"] == {"role": "buyer"}
            and data["native"][0]["response_model"] == "controlled-purchase"
        )
        assert (
            data["native"][0]["reported_usage"]["fields"]["prompt_tokens"]["value"]
            == 10
        )
        assert data["cost_usd"] is None and "synthetic-native-key" not in json.dumps(
            data
        )

    asyncio.run(check())


@pytest.mark.parametrize(
    "kind",
    [
        "length",
        "absent",
        "refusal",
        "tool",
        "empty",
        "unknown-args",
        "boolean-revision",
        "blank-reason",
        "wrong-request",
        "wrong-quote",
    ],
)
def test_native_invalid_role_completion_or_contract_stops_before_later_roles(
    tmp_path, kind
):
    async def check():
        store = setup(tmp_path)
        b = body("buyer")
        if kind == "length":
            b["choices"][0]["finish_reason"] = "length"
        if kind == "absent":
            del b["choices"][0]["finish_reason"]
        if kind == "refusal":
            b["choices"][0]["message"]["refusal"] = "No"
        if kind == "tool":
            b["choices"][0]["message"]["tool_calls"] = [
                {
                    "id": "call",
                    "type": "function",
                    "function": {"name": "approve", "arguments": "{}"},
                }
            ]
        if kind == "empty":
            b["choices"][0]["message"]["content"] = " "
        if kind == "unknown-args":
            b = body("buyer", value={**REC, "approval": "approved"})
        if kind == "boolean-revision":
            b = body("buyer", value={**REC, "expected_quote_revision": True})
        if kind == "blank-reason":
            b = body("buyer", value={**REC, "reason": " "})
        if kind == "wrong-request":
            b = body("buyer", value={**REC, "request_id": "PR-999"})
        if kind == "wrong-quote":
            b = body(
                "buyer", value={**REC, "quote_id": "Q-203", "expected_unit_cents": 1500}
            )
        clients = transports(buyer={"response": b})
        with pytest.raises(BaseException) as error:
            await run(store, clients)
        data = error.value.evidence
        assert (
            data["status"] == "failed"
            and data["native_clients_closed"]
            and data["native_bridge_closed"]
        )
        assert (
            len(clients["buyer"].requests) == 1
            and not clients["checker"].requests
            and not clients["drafter"].requests
        )
        assert not store.read()["proposals"] and all(p.closed for p in clients.values())

    asyncio.run(check())


@pytest.mark.parametrize(
    "usage",
    [
        "missing",
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        {"prompt_tokens": 10, "completion_tokens": 2},
        {"prompt_tokens": -1, "completion_tokens": 2, "total_tokens": 1},
        {"prompt_tokens": True, "completion_tokens": 2, "total_tokens": 3},
    ],
)
def test_raw_usage_preserves_missing_zero_partial_invalid_and_sdk_failure(
    tmp_path, usage
):
    async def check():
        store = setup(tmp_path)
        clients = transports(buyer={"response": body("buyer", usage=usage)})
        try:
            result = await run(store, clients)
            data = json.loads(Path(result["receipt_path"]).read_text())
        except BaseException as error:
            data = error.evidence
        fields = data["native"][0]["reported_usage"]["fields"]
        expected = (
            "unknown"
            if usage == "missing"
            else "invalid"
            if type(usage["prompt_tokens"]) is not int or usage["prompt_tokens"] < 0
            else "observed"
        )
        assert fields["prompt_tokens"]["state"] == expected
        assert fields["total_tokens"]["state"] == (
            "unknown"
            if usage == "missing" or "total_tokens" not in usage
            else "observed"
        )
        assert all(p.closed for p in clients.values()) and data["cost_usd"] is None

    asyncio.run(check())


def test_http_429_is_not_retried_and_failed_role_usage_survives(tmp_path):
    async def check():
        store = setup(tmp_path)
        clients = transports(
            checker={
                "status": 429,
                "response": {
                    "error": {"message": "controlled limit"},
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 0,
                        "total_tokens": 3,
                    },
                },
            }
        )
        with pytest.raises(BaseException) as error:
            await run(store, clients)
        assert len(clients["checker"].requests) == 1 and not clients["drafter"].requests
        assert (
            error.value.evidence["native"][1]["reported_usage"]["fields"][
                "prompt_tokens"
            ]["value"]
            == 3
        )
        assert not store.read()["proposals"]

    asyncio.run(check())


@pytest.mark.parametrize("kind", ["cancel", "deadline"])
def test_pending_native_work_drains_on_cancel_or_deadline(tmp_path, kind):
    async def check():
        store = setup(tmp_path)
        clients = transports(buyer={"wait": asyncio.Event()})
        task = asyncio.create_task(run(store, clients, timeout_seconds=5))
        entered = asyncio.create_task(clients["buyer"].started.wait())
        done, pending = await asyncio.wait(
            {task, entered}, timeout=8, return_when=asyncio.FIRST_COMPLETED
        )
        if not clients["buyer"].started.is_set():
            entered.cancel()
            await asyncio.gather(entered, return_exceptions=True)
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            pytest.fail(
                "native operation settled or stalled before controlled HTTP entry"
            )
        if kind == "cancel":
            task.cancel()
        with pytest.raises(BaseException) as error:
            await task
        assert clients["buyer"].drained.is_set() and all(
            p.closed for p in clients.values()
        )
        assert (
            error.value.evidence["native_clients_closed"]
            and error.value.evidence["native_bridge_closed"]
            and not store.read()["proposals"]
        )

    asyncio.run(check())


def test_source_change_during_native_call_cannot_reach_checker_or_proposal(tmp_path):
    async def check():
        store = setup(tmp_path)
        clients = transports(
            buyer={"hook": lambda: store.update_quote("Q-203", unit_cents=1000)}
        )
        with pytest.raises(BaseException) as error:
            await run(store, clients)
        assert "context changed" in str(error.value) and not clients["checker"].requests
        assert (
            store.read()["quotes"][2]["unit_cents"] == 1000
            and not store.read()["proposals"]
        )

    asyncio.run(check())


def test_cleanup_failure_cannot_become_confirmed_after_an_empty_retry(tmp_path):
    async def check():
        store = setup(tmp_path)
        clients = transports(drafter={"close_error": True})
        with pytest.raises(BaseException) as error:
            await run(store, clients)
        assert (
            not error.value.evidence["native_clients_closed"]
            and not store.read()["proposals"]
            and error.value.evidence["result"] is None
        )

    asyncio.run(check())


@pytest.mark.parametrize("phase", ["proposal_commit_prepared", "completed"])
def test_recording_failure_preserves_before_or_after_proposal_effect(tmp_path, phase):
    async def check():
        store = setup(tmp_path)
        clients = transports()

        def writer(path, data):
            if data["status"] in {phase, "failed"}:
                raise OSError("controlled persistent recording fault")
            path.write_text(json.dumps(data))

        with pytest.raises(BaseException) as error:
            await run(store, clients, writer=writer)
        assert (
            error.value.evidence["status"] == "failed"
            and error.value.evidence["durable_status"] == "unknown"
        )
        assert len(store.read()["proposals"]) == (1 if phase == "completed" else 0)
        assert (
            error.value.evidence["native_clients_closed"]
            and error.value.evidence["native_bridge_closed"]
        )

    asyncio.run(check())


def test_native_missing_key_retains_prepared_failure_without_borrowing_a_provider(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("PURCHASE_MODEL", "openai/controlled")

    async def check():
        store = setup(tmp_path)
        with pytest.raises(ValueError) as error:
            await run_team_async(store, "PR-101")
        assert (
            error.value.evidence["native"] == []
            and error.value.evidence["native_clients_closed"]
            and error.value.evidence["status"] == "failed"
        )

    asyncio.run(check())
