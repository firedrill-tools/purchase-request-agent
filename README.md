# Purchase request agent with CrewAI and OpenAI

A three-role CrewAI workflow compares supplier quotes for a synthetic purchase request, checks stock and budget, and drafts a proposal for a named human decision. The model cannot approve or place an order. A deterministic store checks the recommendation against current quotes and requires explicit approval before a local purchase order is recorded.

## Simulate with Firedrill

[Firedrill](https://firedrill.run/) is a simulation platform for AI agents.
Start from this repository’s root:

```sh
npx firedrill-run init
```

Sign in and choose your project, then follow the [simulation run guide](firedrill/README.md)
for application dependencies, a preview and the reviewed scenarios. Your agent runs
locally or in your CI; model credentials stay separate from Firedrill sign-in.
The included recipes record outcomes without an automatic quality score or CI gate.

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

The named approver is a synthetic fixture identity, not real authentication. `commit` rechecks the request, quote revision, unit price, available stock, total, and approval. It decreases synthetic stock and records a single local order. Use `update-quote --quote Q-201 --unit-cents 1600` or `--stock 5` between proposal and commit to exercise changed conditions. No real supplier service or payment rail is used. Cooperating processes serialize complete read/recheck/atomic replacement with a persistent SQLite mutex. New initialized state has an instance ID that fences stale owners. Reset materializes the original case into a fresh destination and leaves old state/evidence intact. Uncooperative filesystem writers and arbitrary hostile processes are outside an OS security boundary.

The fixture contains an eligible lower-cost quote, a cheaper quote with insufficient stock, an eligible but more expensive quote, and an over-budget quote. The actual three-role CrewAI/OpenAI HTTP path and separately labeled local stub path are qualified. Strict per-role recommendations and exact buyer/checker/drafter handoffs precede proposal creation. Controlled responses verify transport and deterministic boundaries; they do not establish live model quality. Live provider proof and authenticated persisted Firedrill runs remain pending. The private repository has no license pending publication decisions.

Explore related applications in the [Firedrill example catalog](https://github.com/firedrill-tools/firedrill-example-catalog). Product and setup references: [Firedrill](https://firedrill.run/) and the [official developer documentation](https://docs.firedrill.run/). Use documentation matching the qualified release; this repository records its current integration limits above.


## Current source and native qualification

81 checks cover strict bounded JSON/state/identity/reference/non-boolean integer contracts; reviewed request and complete quote population snapshots; proposal content SHA256 and an explicit synthetic decision bound to that exact proposal. Current SKU, quantity, currency, price, stock, budget, competing quote population and both revisions are checked again at commit. A changed quote population requires a new review; no fallback changes the reviewed proposal. Historic proposals without source/decision bindings are rejected without silent migration. Original fixture currency is unavailable (`null`), never inferred; both original sides compare in the same unspecified cents unit. New explicit currencies must agree. A fixture approver name is a local synthetic decision marker, not authentication.

Native configuration uses pinned CrewAI1.15.28/OpenAI2.54.0/HTTPX0.28.1/Pydantic2.12.5. Explicit key/base URL and no environment proxy/project borrowing, one prepared HTTP per role/2048 requested output tokens, zero SDK/framework retries,30-second phases and120-second default operation budget qualify the owned path. `--base-url` accepts explicit HTTPS or owned loopback HTTP; `--timeout-seconds` is at most300. The original response must be one coherent stop-finished text result before SDK/role release. Unfinished/refused/tool output, malformed recommendations, unsupported fields and changed source stop later roles. Raw decoded bytes/JSON/unknown fields/model/IDs/usage remain separate from SDK normalized counters; missing/zero/partial/invalid usage and unknown cost are retained on failures. Response caps are after SDK buffering, not hard wire/heap limits; synchronous local operations can delay budgets.

CrewAI's async Flow currently uses a synchronous native call seam. A tracked run-loop bridge owns actual async OpenAI requests, propagates failures through framework retry loops and drains before clients close. Native clients and actual task SQLite are private to the run, including injected-LLM crew storage. No shared latest-kickoff store is constructed/reset. The narrow native subclass only supplies the owned async client and sync bridge; native request formatting/parsing is inherited without vendor/global-cache patches.

Private prepared/partial/final receipts under the workspace's `runs/` directory (`--output-root`) contain original source bytes/hash/context, all role/raw HTTP outputs, usage, cleanup status and proposal effects. Recording/source/startup/deadline/cancel/close failures block further work and retain actual partial effects. A saved proposal remains pending if final evidence fails; success is not fabricated. Actual normal CLI HTTP and SIGINT/SIGTERM/SIGHUP drain paths are tested. CLI diagnostics use stderr; stdout contains one JSON result. Recommend without `--live` is a key-free, read-only plan. A real run requires `--live --max-provider-requests 3 --allow-writes`; only a synthetic proposal is authorized by those flags. Approval and commit remain separate commands with no model tool route.

## Datasets and distribution

```sh
uv run python -m purchase_request datasets
uv run python -m purchase_request init --state run-output/busy.json --case busy-procurement
uv run python -m purchase_request workspace --state run-output/busy.json
uv run python -m purchase_request reset --state run-output/busy.json --destination run-output/busy-fresh.json
uv run python scripts/build_datasets.py --check
uv build
uv run python scripts/check_distribution.py
```

`purchase-populations` 1.0.0 contains twelve hash-bound cases in four profiles. Busy has 20 requests, 80 quotes and four imported orders; stock and revisions reconcile with original allocation history. Missing currency, hostile supplier notes, changed price, insufficient stock, over-budget totals, lexical ties and exact one-cent/maximum-safe values have distinct cases. Imported history explicitly lacks prior model, human authentication and real supplier delivery evidence. Fixture/task labels, source history and scoring expectations stay outside role context. All twelve populations run the actual native three-role SDK with controlled responses, and each recommended case separately exercises synthetic approval and order commit afterward.

Initialization exclusively creates private state/source metadata; readback distinguishes valid current edits from the seed. Reset creates a new instance, keeps the original state/receipts, and refuses collisions. Missing, corrupt or foreign authority is refused without repair. A process crash during a multi-file initialization can leave an incomplete destination; inspect it and use a fresh destination. No automatic cleanup removes unknown work.

Fresh locked wheel, sdist and complete-source installs outside checkout qualify console/module CLI, every case/readback/reset, real installed three-role native CLI followed by a separate local human decision/order, missing-key failure and default key-free plan. The complete archived suite has 81 checks; original delivered hashes stay unchanged and repeated source bytes agree. The archive includes code/data/original fixture/tests/scripts/scenarios/docs/CI/lockfiles plus exact SHA256/size/mode manifest, excluding private state, Git and installed caches. CI retains source and qualification.json; exact source/CI/artifact proof is recorded in the portfolio catalog. See COMPATIBILITY.md for evidence limits.

No paid provider or real supplier/order/payment action was used. Live semantics/billing, authenticated persisted Firedrill runs and publication/license remain parked. Those external gates do not block the independent source and distribution work.

## Current Firedrill integration

Released CLI/SDK 2.2.2, current command invocation, remote operator MCP and recovery are documented in [firedrill/README.md](firedrill/README.md). Offline checks preserve actual context/native key behavior and independent proposal permission; human decisions, authenticated persisted runs and live quality/cost remain separate.
