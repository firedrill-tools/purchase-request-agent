"""Private source, role, failure and partial-effect evidence."""

import asyncio
import base64
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .schema import canonical


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    return hashlib.sha256(
        value if isinstance(value, bytes) else canonical(value).encode()
    ).hexdigest()


class Receipt:
    sha = staticmethod(sha)

    def __init__(self, store, request_id, output_root, timeout, writer=None):
        self.deadline = time.monotonic() + timeout
        self.writer = writer
        self.recording_error = None
        self.failure = None
        self.stopping = False
        self.store = store
        state, raw, identity = store.snapshot()
        self.context = store.context(request_id)
        self.request_id = request_id
        root = Path(output_root or store.path.parent / "runs")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink():
            raise ValueError("receipt root must be a local directory")
        directory = root / str(uuid.uuid4())
        directory.mkdir(mode=0o700)
        self.path = directory / "trace.json"
        self.data = {
            "schema_version": 1,
            "run_id": directory.name,
            "status": "prepared",
            "started_at": now(),
            "source_state_sha256": sha(raw),
            "source_state_base64": base64.b64encode(raw).decode(),
            "source_context": self.context,
            "context_sha256": sha(self.context),
            "roles": [],
            "native": [],
            "role_outputs": [],
            "crew_usage": None,
            "native_clients_closed": False,
            "native_bridge_closed": False,
            "result": None,
            "error": None,
            "cost_usd": None,
            "limits": {
                "prepared_requests": 3,
                "per_role_output_tokens": 2048,
                "retries": 0,
                "http_seconds": 30,
                "operation_seconds": timeout,
            },
        }

    def guard(self):
        if self.stopping:
            raise asyncio.CancelledError("purchase work is stopping")
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None and task.cancelling():
            raise asyncio.CancelledError("purchase cancellation requested")
        if self.recording_error:
            raise self.recording_error
        if self.failure:
            raise RuntimeError("prior native boundary failed") from self.failure
        if time.monotonic() >= self.deadline:
            raise TimeoutError("purchase operation deadline exceeded")

    def source_guard(self):
        self.guard()
        if canonical(self.store.context(self.request_id)) != canonical(self.context):
            raise ValueError(
                "original procurement context changed during recommendation"
            )
        self.guard()

    def save(self, final=False):
        if self.recording_error and not final:
            raise self.recording_error
        staged = self.path.with_name("." + str(uuid.uuid4()) + ".tmp")
        try:
            value = json.loads(json.dumps(self.data, allow_nan=False))
            if self.writer is not None:
                self.writer(self.path, value)
                return
            fd = os.open(staged, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(
                    (
                        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)
                        + "\n"
                    ).encode()
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, self.path)
            fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except BaseException as error:
            self.recording_error = error
            raise
        finally:
            staged.unlink(missing_ok=True)

    def finish(self, result, failure):
        self.data.update(
            status="failed" if failure else "completed",
            finished_at=now(),
            result=None if failure else result,
        )
        try:
            _, raw, _ = self.store.snapshot()
            self.data.update(
                final_state_sha256=sha(raw),
                final_state_base64=base64.b64encode(raw).decode(),
            )
        except BaseException as error:
            self.data["final_state_error"] = {
                "name": type(error).__name__,
                "message": str(error),
            }
        if failure:
            chain = []
            seen = set()
            current = failure
            while current is not None and id(current) not in seen and len(chain) < 8:
                seen.add(id(current))
                chain.append(
                    {
                        "name": type(current).__name__,
                        "message": str(current),
                        "status_code": getattr(current, "status_code", None),
                    }
                )
                current = current.__cause__
            self.data["error"] = {
                "name": type(failure).__name__,
                "message": str(failure),
                "causes": chain,
            }
            for item in self.data["native"]:
                if item["status"] in {"prepared", "received"}:
                    item.update(status="failed", error=self.data["error"])
        self.save(final=True)
