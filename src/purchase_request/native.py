"""Owned native CrewAI/OpenAI completions, before SDK role release."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from openai import AsyncOpenAI, omit
from pydantic import PrivateAttr, model_validator
from crewai.llms.providers.openai.completion import OpenAICompletion

from .crew import ROLES
from .store import strict_json

MAX_BODY = 1048576


@dataclass(frozen=True)
class RoleConfig:
    role: str
    model: str
    api_key: str = field(repr=False)
    base_url: str = "https://api.openai.com/v1"

    def validate(self):
        if (
            self.role not in ROLES
            or not isinstance(self.model, str)
            or not self.model
            or len(self.model) > 160
            or any(c.isspace() for c in self.model)
        ):
            raise ValueError("explicit buyer/checker/drafter model is required")
        if (
            not isinstance(self.api_key, str)
            or not self.api_key
            or len(self.api_key) > 4096
            or any(not 33 <= ord(c) <= 126 for c in self.api_key)
        ):
            raise ValueError(f"explicit {self.role} OpenAI key is required")
        u = urlsplit(self.base_url)
        if (
            not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
            or not (
                u.scheme == "https"
                or u.scheme == "http"
                and u.hostname in {"localhost", "127.0.0.1", "::1"}
            )
        ):
            raise ValueError(
                "endpoint needs HTTPS or owned loopback HTTP without URL credentials/query/fragment"
            )
        u.port
        return self

    def public(self):
        u = urlsplit(self.base_url)
        return {
            "role": self.role,
            "provider": "openai",
            "model": self.model,
            "endpoint_origin": f"{u.scheme}://{u.netloc}",
            "base_path": u.path,
        }


def from_environment(model=None, base_url=None):
    selected = model or os.getenv("PURCHASE_MODEL")
    if not selected or not selected.startswith("openai/"):
        raise ValueError(
            "PURCHASE_MODEL requires openai/<model>; other providers are not qualified"
        )
    key = os.getenv("OPENAI_API_KEY")
    return [
        RoleConfig(
            role, selected[7:], key, base_url or "https://api.openai.com/v1"
        ).validate()
        for role in ROLES
    ]


class OwnedOpenAI(OpenAICompletion):
    _owned = PrivateAttr()
    _bridge = PrivateAttr()

    def call(self, *args, **kwargs):
        return self._bridge.invoke(self.acall, *args, **kwargs)

    @model_validator(mode="after")
    def _init_clients(self):
        return self

    def _get_async_client(self):
        return self._owned

    def _get_sync_client(self):
        raise RuntimeError("this role owns native async work only")


def completion(body):
    if not isinstance(body, dict):
        raise ValueError("provider response must be an object")
    choices = body.get("choices")
    if (
        body.get("object") != "chat.completion"
        or not isinstance(body.get("id"), str)
        or not body["id"].strip()
        or not isinstance(choices, list)
        or len(choices) != 1
        or not isinstance(choices[0], dict)
    ):
        raise ValueError("expected one original chat completion")
    choice = choices[0]
    message = choice.get("message")
    if (
        type(choice.get("index")) is not int
        or choice["index"] != 0
        or choice.get("finish_reason") != "stop"
        or not isinstance(message, dict)
        or message.get("role") != "assistant"
        or message.get("refusal")
        or message.get("tool_calls")
        or message.get("function_call")
        or not isinstance(message.get("content"), str)
        or not message["content"].strip()
    ):
        raise ValueError(
            "role requires completed stop text without pending/refused/tool output"
        )
    return {
        "response_id": body["id"],
        "response_model": body.get("model"),
        "finish_reason": choice["finish_reason"],
    }


def measurements(body):
    raw = body.get("usage") if isinstance(body, dict) else None
    fields = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = raw.get(name) if isinstance(raw, dict) else None
        valid = type(value) is int and 0 <= value <= 2**53 - 1
        fields[name] = {
            "state": "observed" if valid else "unknown" if value is None else "invalid",
            "value": value if valid else None,
        }
    return {
        "source": "original decoded HTTP JSON before SDK normalization",
        "raw": raw,
        "fields": fields,
        "cost_usd": None,
    }


class Boundary:
    def __init__(self, config, receipt):
        self.config, self.receipt, self.observation = config, receipt, None

    async def request(self, request):
        self.receipt.guard()
        self.receipt.source_guard()
        if self.observation is not None:
            raise RuntimeError(
                "one prepared HTTP request per role; retry/repair disabled"
            )
        if (
            request.method != "POST"
            or str(request.url)
            != self.config.base_url.rstrip("/") + "/chat/completions"
        ):
            raise ValueError("unqualified provider request")
        raw = await request.aread()
        if len(raw) > MAX_BODY:
            raise ValueError("role request exceeds retained bound")
        body = strict_json(raw)
        if (
            body.get("stream")
            or body.get("model") != self.config.model
            or body.get("max_completion_tokens") != 2048
        ):
            raise ValueError("unqualified native request settings")
        self.observation = {
            "role": self.config.role,
            "provider": "openai",
            "status": "prepared",
            "request_sha256": self.receipt.sha(raw),
            "request_body": body,
            "response": None,
            "error": None,
        }
        self.receipt.data["native"].append(self.observation)
        self.receipt.save()
        self.receipt.guard()
        self.receipt.source_guard()

    async def response(self, response):
        item = self.observation
        item.update(
            status="received",
            http_status=response.status_code,
            request_id=response.headers.get("x-request-id"),
        )
        try:
            raw = await response.aread()
            item.update(
                decoded_body_base64=base64.b64encode(raw[:MAX_BODY]).decode(),
                body_sha256=self.receipt.sha(raw),
                decoded_bytes=len(raw),
                truncated=len(raw) > MAX_BODY,
            )
            if len(raw) > MAX_BODY:
                self.receipt.save()
                raise ValueError("provider response exceeds retained bound")
            body = strict_json(raw.decode("utf-8"))
            item.update(response=body, reported_usage=measurements(body))
            self.receipt.save()
            self.receipt.guard()
            self.receipt.source_guard()
            if response.status_code == 200:
                item.update(completion(body))
                item["status"] = "accepted"
            else:
                item["status"] = "http_error"
            self.receipt.save()
            self.receipt.guard()
        except BaseException as error:
            self.receipt.failure = self.receipt.failure or error
            item.update(
                status="rejected",
                error={"name": type(error).__name__, "message": str(error)},
            )
            self.receipt.save()
            raise
        finally:
            await response.aclose()


async def create_role(config, receipt, stack, transport=None, bridge=None):
    config.validate()
    boundary = Boundary(config, receipt)
    client = await stack.enter_async_context(
        httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(30, connect=5),
            trust_env=False,
            follow_redirects=False,
            event_hooks={
                "request": [boundary.request],
                "response": [boundary.response],
            },
        )
    )
    sdk = await stack.enter_async_context(
        AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            http_client=client,
            max_retries=0,
            organization=None,
            project=None,
            default_headers={"OpenAI-Organization": omit, "OpenAI-Project": omit},
        )
    )
    llm = OwnedOpenAI(
        model=config.model,
        provider="openai",
        api_key=config.api_key,
        base_url=config.base_url,
        max_retries=0,
        max_completion_tokens=2048,
        stream=False,
        temperature=0,
        timeout=30,
    )
    llm._owned = sdk
    llm._bridge = bridge
    return llm, boundary
