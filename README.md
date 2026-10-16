# Purchase request agent

A three-role CrewAI workflow compares supplier quotes for a synthetic purchase request, checks stock and budget, and drafts a proposal for a named human decision. The model cannot approve or place an order. A deterministic store checks the recommendation against current quotes and requires explicit approval before a local purchase order is recorded.

## Run

Requires Python 3.12+ and `uv`.

```sh
uv sync --extra test
uv run python -m purchase_request init --state run-output/purchase.json
uv run python -m purchase_request context --state run-output/purchase.json
uv run pytest
```

For a live crew run, set `PURCHASE_MODEL=openai/<enabled-model>` and provide `OPENAI_API_KEY`. This qualified runtime supports OpenAI explicitly; other CrewAI providers have no owned runtime proof here. Then:

```sh
uv run python -m purchase_request recommend --state run-output/purchase.json --request PR-101 --allow-writes --live --max-provider-requests 3
```

The agent recommendation is independently checked: exact SKU and quote revision, enough stock, total within budget, and the least expensive eligible quote. A successful recommendation creates `PROP-1` with approval `pending`, never an order. The synthetic human decision and order steps are separate:

```sh
uv run python -m purchase_request decide --state run-output/purchase.json --proposal PROP-1 --approver "Ava Chen" --decision approved
uv run python -m purchase_request commit --state run-output/purchase.json --proposal PROP-1 --allow-writes
```

The named approver is a synthetic fixture identity, not real authentication. `commit` rechecks the request, quote revision, unit price, available stock, total, and approval. It decreases synthetic stock and records a single local order. Use `update-quote --quote Q-201 --unit-cents 1600` or `--stock 5` between proposal and commit to exercise changed conditions. No real supplier service or payment rail is used. Cooperating processes serialize complete read/recheck/atomic replacement with a persistent SQLite mutex. New initialized state has an instance ID that fences stale owners. Reset currently means initializing a fresh destination; old state/evidence is preserved. Uncooperative filesystem writers and arbitrary hostile processes are outside an OS security boundary.

The fixture contains an eligible lower-cost quote, a cheaper quote with insufficient stock, an eligible but more expensive quote, and an over-budget quote. The actual three-role CrewAI/OpenAI HTTP path and separately labeled local stub path are qualified. Strict per-role recommendations and exact buyer/checker/drafter handoffs precede proposal creation. Controlled responses verify transport and deterministic boundaries; they do not establish live model quality. Live provider proof and the released Firedrill integration remain pending. The private repository has no license pending publication decisions.

Explore related applications in the [Firedrill example catalog](https://github.com/firedrill-tools/firedrill-example-catalog). Product and setup references: [Firedrill](https://firedrill.run/) and the [official developer documentation](https://docs.firedrill.run/). Use documentation matching the qualified release; this repository records its current integration limits above.


## Current source and native qualification

53 local checks cover strict bounded JSON/state/identity/reference/non-boolean integer contracts; reviewed request and complete quote population snapshots; proposal content SHA256 and an explicit synthetic decision bound to that exact proposal. Current SKU, quantity, currency, price, stock, budget, competing quote population and both revisions are checked again at commit. A changed quote population requires a new review; no fallback changes the reviewed proposal. Historic proposals without source/decision bindings are rejected without silent migration. Original fixture currency is unavailable (`null`), never inferred; both original sides compare in the same unspecified cents unit. New explicit currencies must agree. A fixture approver name is a local synthetic decision marker, not authentication.

Native configuration uses pinned CrewAI1.15.28/OpenAI2.54.0/HTTPX0.28.1/Pydantic2.12.5. Explicit key/base URL and no environment proxy/project borrowing, one prepared HTTP per role/2048 requested output tokens, zero SDK/framework retries,30-second phases and120-second default operation budget qualify the owned path. `--base-url` accepts explicit HTTPS or owned loopback HTTP; `--timeout-seconds` is at most300. The original response must be one coherent stop-finished text result before SDK/role release. Unfinished/refused/tool output, malformed recommendations, unsupported fields and changed source stop later roles. Raw decoded bytes/JSON/unknown fields/model/IDs/usage remain separate from SDK normalized counters; missing/zero/partial/invalid usage and unknown cost are retained on failures. Response caps are after SDK buffering, not hard wire/heap limits; synchronous local operations can delay budgets.

CrewAI's async Flow currently uses a synchronous native call seam. A tracked run-loop bridge owns actual async OpenAI requests, propagates failures through framework retry loops and drains before clients close. Native clients and actual task SQLite are private to the run, including injected-LLM crew storage. No shared latest-kickoff store is constructed/reset. The narrow native subclass only supplies the owned async client and sync bridge; native request formatting/parsing is inherited without vendor/global-cache patches.

Private prepared/partial/final receipts under the workspace's `runs/` directory (`--output-root`) contain original source bytes/hash/context, all role/raw HTTP outputs, usage, cleanup status and proposal effects. Recording/source/startup/deadline/cancel/close failures block further work and retain actual partial effects. A saved proposal remains pending if final evidence fails; success is not fabricated. Actual normal CLI HTTP and SIGINT/SIGTERM/SIGHUP drain paths are tested. CLI diagnostics use stderr; stdout contains one JSON result. Recommend without `--live` is a key-free, read-only plan. A real run requires `--live --max-provider-requests 3 --allow-writes`; only a synthetic proposal is authorized by those flags. Approval and commit remain separate commands with no model tool route.

Coherent rich versioned datasets, private model-free workspace readback/reset, fresh locked wheel/sdist/complete-source distribution and their exact Linux source/artifact proof remain active tasks in PLAN.md. No independent-completion claim is made yet. No paid provider or real supplier/payment action was used.
