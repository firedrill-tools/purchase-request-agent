import json
import os
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from purchase_request.store import Store

FIXTURE = Path(__file__).parent.parent / "fixtures" / "starter.json"
REC = {
    "request_id": "PR-101",
    "quote_id": "Q-201",
    "expected_unit_cents": 1400,
    "expected_quote_revision": 1,
    "reason": "Lowest eligible quote",
}


@contextmanager
def server(mode):
    requests = []
    started = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(
                {
                    "body": json.loads(raw),
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                }
            )
            started.set()
            if mode == "pending":
                release.wait(10)
                return
            b = {
                "id": f"chatcmpl-{len(requests)}",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "controlled-cli",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(REC),
                            "refusal": None,
                        },
                        "finish_reason": "length" if mode == "length" else "stop",
                        "logprobs": None,
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            }
            encoded = json.dumps(b).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}/v1", requests, started
    finally:
        release.set()
        http.shutdown()
        http.server_close()
        thread.join()


def command(file, url):
    return [
        sys.executable,
        "-m",
        "purchase_request",
        "recommend",
        "--state",
        str(file),
        "--allow-writes",
        "--live",
        "--max-provider-requests",
        "3",
        "--model",
        "openai/controlled",
        "--base-url",
        url,
    ]


def environment():
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = "synthetic-cli-key"
    env["CREWAI_TRACING_ENABLED"] = "false"
    env["OTEL_SDK_DISABLED"] = "true"
    return env


def test_normal_native_cli_preserves_three_role_advisory_boundary(tmp_path):
    file = tmp_path / "state.json"
    file.write_bytes(FIXTURE.read_bytes())
    with server("ok") as (url, requests, _):
        result = subprocess.run(
            command(file, url),
            env=environment(),
            capture_output=True,
            text=True,
            timeout=20,
        )
    assert result.returncode == 0, result.stderr + result.stdout
    output = json.loads(result.stdout)
    assert output["ok"] and output["proposal"]["approval"] == "pending"
    assert len(requests) == 3 and all(
        r["path"] == "/v1/chat/completions" and r["auth"] == "Bearer synthetic-cli-key"
        for r in requests
    )
    assert Store(file).read()["orders"] == []
    assert json.loads(Path(output["receipt_path"]).read_text())["native_clients_closed"]


@pytest.mark.parametrize("mode", ["length", "missing"])
def test_native_cli_failures_retain_private_receipts(tmp_path, mode):
    file = tmp_path / "state.json"
    file.write_bytes(FIXTURE.read_bytes())
    with server("length") as (url, requests, _):
        env = environment()
        if mode == "missing":
            env["OPENAI_API_KEY"] = ""
        result = subprocess.run(
            command(file, url), env=env, capture_output=True, text=True, timeout=20
        )
    assert result.returncode == 1, result.stderr + result.stdout
    output = json.loads(result.stdout)
    data = json.loads(Path(output["receipt_path"]).read_text())
    assert (
        data["status"] == "failed"
        and data["native_clients_closed"]
        and data["native_bridge_closed"]
    )
    assert (
        len(requests) == (0 if mode == "missing" else 1)
        and Store(file).read()["proposals"] == []
    )


@pytest.mark.parametrize("name", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_native_cli_signal_drains_role_calls_and_clients(tmp_path, name):
    file = tmp_path / "state.json"
    file.write_bytes(FIXTURE.read_bytes())
    with server("pending") as (url, requests, started):
        child = subprocess.Popen(
            command(file, url),
            env=environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert started.wait(15), "native CLI failed to reach owned HTTP"
            child.send_signal(name)
            stdout, stderr = child.communicate(timeout=15)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()
    assert child.returncode == 128 + name, stderr + stdout
    output = json.loads(stdout)
    assert (
        output["native_clients_closed"]
        and output["native_bridge_closed"]
        and len(requests) == 1
    )
    assert Store(file).read()["proposals"] == []


def test_init_is_private_exclusive_and_default_recommendation_is_key_free(tmp_path):
    file = tmp_path / "state.json"
    for n in range(2):
        result = subprocess.run(
            [sys.executable, "-m", "purchase_request", "init", "--state", str(file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == (0 if n == 0 else 2)
    assert file.stat().st_mode & 0o777 == 0o600
    before = file.read_bytes()
    result = subprocess.run(
        [sys.executable, "-m", "purchase_request", "recommend", "--state", str(file)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (
        result.returncode == 0
        and json.loads(result.stdout)["status"] == "plan"
        and file.read_bytes() == before
    )
