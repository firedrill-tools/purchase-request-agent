"""Fresh locked wheel/sdist/complete-source exercise outside the checkout."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path
from zipfile import ZipFile
from package_source import build

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
UV = os.environ.get("UV") or shutil.which("uv")
if not UV:
    raise RuntimeError("uv is required")
ENV = {**os.environ, "CREWAI_TRACING_ENABLED": "false", "OTEL_SDK_DISABLED": "true"}
for key in (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "PURCHASE_MODEL",
    "VIRTUAL_ENV",
    "PYTHONPATH",
    "PYTHONHOME",
):
    ENV.pop(key, None)


def run(command, cwd, check=True):
    result = subprocess.run(
        [str(x) for x in command],
        cwd=cwd,
        env=ENV,
        capture_output=True,
        text=True,
        timeout=240,
    )
    if check and result.returncode:
        raise RuntimeError(
            f"command failed: {command[0]}\n{result.stdout[-12000:]}\n{result.stderr[-12000:]}"
        )
    return result


PROGRAM = r"""
import json,os,subprocess,sys,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import purchase_request
from purchase_request.datasets import populations,initialize,workspace_info,reset
from purchase_request.store import Store
base=Path(sys.argv[1]);assert Path(purchase_request.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
for population in populations():
 file=base/(population['id']+'.json');created=initialize(file,population['id']);store=Store(file);assert workspace_info(file,store.read())['original_seed_match'];assert file.stat().st_mode&0o777==0o600
 before=file.read_bytes();fresh=reset(file,base/(population['id']+'.fresh.json'),store.read());assert fresh['instance_id']!=created['instance_id'] and file.read_bytes()==before
file=base/'original-quotes.json';requests=[]
rec={'request_id':'PR-101','quote_id':'Q-201','expected_unit_cents':1400,'expected_quote_revision':1,'reason':'Controlled installed comparison; human decision remains separate.'}
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));requests.append(body);assert self.path=='/v1/chat/completions' and self.headers.get('Authorization')=='Bearer synthetic-installed-key' and body['max_completion_tokens']==2048
  raw=json.dumps({'id':f'chatcmpl-{len(requests)}','object':'chat.completion','created':1700000000,'model':'controlled-installed','choices':[{'index':0,'message':{'role':'assistant','content':json.dumps(rec),'refusal':None},'finish_reason':'stop','logprobs':None}],'usage':{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}}).encode()
  self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True;thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
 command=[sys.executable,'-I','-m','purchase_request','recommend','--state',str(file),'--request','PR-101','--allow-writes','--live','--max-provider-requests','3','--model','openai/controlled','--base-url',f'http://127.0.0.1:{server.server_port}/v1'];child=subprocess.run(command,env={**os.environ,'OPENAI_API_KEY':'synthetic-installed-key'},capture_output=True,text=True,timeout=30);assert child.returncode==0,child.stdout+child.stderr
 output=json.loads(child.stdout);assert output['ok'] and output['proposal']['approval']=='pending' and len(requests)==3 and not Store(file).read()['orders'];trace=json.loads(Path(output['receipt_path']).read_text());assert trace['native_clients_closed'] and trace['native_bridge_closed'] and all(row['reported_usage']['fields']['prompt_tokens']['value']==10 for row in trace['native']) and trace['cost_usd'] is None
 store=Store(file);assert store.decide(output['proposal']['id'],'Ava Chen','approved')['ok'];assert store.commit(output['proposal']['id'])['ok'];assert len(store.read()['orders'])==1
finally:server.shutdown();server.server_close();thread.join(2)
print('installed twelve populations/readback/reset, actual native three-role CLI and separate human decision/order passed')
"""


def exercise(python, base):
    base.mkdir()
    console = python.parent / "purchase-request"
    run([console, "--help"], base)
    cases = json.loads(
        run([python, "-I", "-m", "purchase_request", "datasets"], base).stdout
    )
    assert len(cases) == 12
    file = base / "dry.json"
    run([console, "init", "--state", file], base)
    before = file.read_bytes()
    assert (
        json.loads(run([console, "recommend", "--state", file], base).stdout)["status"]
        == "plan"
        and file.read_bytes() == before
    )
    missing = run(
        [
            console,
            "recommend",
            "--state",
            file,
            "--live",
            "--max-provider-requests",
            "3",
            "--model",
            "openai/controlled",
            "--allow-writes",
        ],
        base,
        check=False,
    )
    assert missing.returncode == 1
    output = json.loads(missing.stdout)
    trace = json.loads(Path(output["receipt_path"]).read_text())
    assert (
        trace["native"] == []
        and trace["status"] == "failed"
        and trace["native_clients_closed"]
        and trace["cost_usd"] is None
    )
    run([python, "-I", "-c", PROGRAM, base], base)


def extract(file, destination):
    destination.mkdir()
    with tarfile.open(file) as archive:
        assert all(m.isfile() or m.isdir() for m in archive.getmembers())
        archive.extractall(destination, filter="data")
    (source,) = destination.iterdir()
    return source


def verify_source(source):
    manifest = json.loads((source / "source-manifest.json").read_text())
    assert {str(p.relative_to(source)) for p in source.rglob("*") if p.is_file()} == {
        row["path"] for row in manifest["files"]
    } | {"source-manifest.json"}
    for row in manifest["files"]:
        p = source / row["path"]
        assert (
            hashlib.sha256(p.read_bytes()).hexdigest() == row["sha256"]
            and p.stat().st_size == row["bytes"]
            and p.stat().st_mode & 0o777 == row["mode"]
        )
    return manifest


def main():
    wheel = ROOT / "dist" / f"purchase_request_agent-{VERSION}-py3-none-any.whl"
    sdist = ROOT / "dist" / f"purchase_request_agent-{VERSION}.tar.gz"
    bundle = build()
    with tempfile.TemporaryDirectory(prefix="purchase-distribution-") as directory:
        temp = Path(directory)
        assert not temp.is_relative_to(ROOT)
        assert build(temp / "repeat.tar.gz")["sha256"] == bundle["sha256"]
        with ZipFile(wheel) as archive:
            assert all(
                p in archive.namelist()
                for p in (
                    "purchase_request/sample_data/index.json",
                    "purchase_request/original_data/starter.json",
                )
            )
        requirements = temp / "requirements.txt"
        requirements.write_text(
            run(
                [
                    UV,
                    "export",
                    "--locked",
                    "--extra",
                    "test",
                    "--no-dev",
                    "--no-emit-project",
                    "--format",
                    "requirements-txt",
                ],
                ROOT,
            ).stdout
        )
        environment = temp / "wheel-env"
        run([UV, "venv", "--python", sys.executable, environment], temp)
        python = environment / "bin/python"
        run([UV, "pip", "sync", "--python", python, requirements], temp)
        run([UV, "pip", "install", "--python", python, "--no-deps", wheel], temp)
        exercise(python, temp / "wheel-case")
        source = extract(sdist, temp / "sdist")
        assert all(
            (source / name).exists()
            for name in (
                "uv.lock",
                "data/index.json",
                "fixtures/starter.json",
                "scripts/build_datasets.py",
                "tests/test_cli_native.py",
                ".github/workflows/check.yml",
                "COMPATIBILITY.md",
            )
        )
        run(
            [
                UV,
                "sync",
                "--locked",
                "--extra",
                "test",
                "--no-editable",
                "--project",
                source,
            ],
            temp,
        )
        exercise(source / ".venv/bin/python", temp / "sdist-case")
        run(
            [source / ".venv/bin/python", "scripts/build_datasets.py", "--check"],
            source,
        )
        complete = extract(bundle["path"], temp / "complete")
        manifest = verify_source(complete)
        run(
            [
                UV,
                "sync",
                "--locked",
                "--extra",
                "test",
                "--no-editable",
                "--project",
                complete,
            ],
            temp,
        )
        exercise(complete / ".venv/bin/python", temp / "complete-case")
        run(
            [complete / ".venv/bin/python", "scripts/build_datasets.py", "--check"],
            complete,
        )
        suite = run(
            [complete / ".venv/bin/python", "-m", "pytest", "-q", "--disable-warnings"],
            complete,
        )
        assert "81 passed" in suite.stdout, suite.stdout
        for row in manifest["files"]:
            assert (
                hashlib.sha256((complete / row["path"]).read_bytes()).hexdigest()
                == row["sha256"]
            )
    report = {
        **bundle,
        "checks": "fresh locked wheel/sdist/complete source, twelve private populations/readback/reset, installed native three-role CLI and separate synthetic human decision/order, dry plan/missing-key receipts, complete81 suite, unchanged delivered files/repeated bytes",
    }
    (ROOT / "dist/qualification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
