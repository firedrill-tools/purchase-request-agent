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

For a live crew run, set `PURCHASE_MODEL` to a CrewAI-supported provider/model string and provide that provider's key. Then:

```sh
uv run python -m purchase_request recommend --state run-output/purchase.json --request PR-101 --allow-writes
```

The agent recommendation is independently checked: exact SKU and quote revision, enough stock, total within budget, and the least expensive eligible quote. A successful recommendation creates `PROP-1` with approval `pending`, never an order. The synthetic human decision and order steps are separate:

```sh
uv run python -m purchase_request decide --state run-output/purchase.json --proposal PROP-1 --approver "Ava Chen" --decision approved
uv run python -m purchase_request commit --state run-output/purchase.json --proposal PROP-1 --allow-writes
```

The named approver is a synthetic fixture identity, not real authentication. `commit` rechecks the request, quote revision, unit price, available stock, total, and approval. It decreases synthetic stock and records a single local order. Use `update-quote --quote Q-201 --unit-cents 1600` or `--stock 5` between proposal and commit to exercise changed conditions. No real supplier service or payment rail is used. State is a single-process local JSON file; reset by initializing a new file.

The fixture contains an eligible lower-cost quote, a cheaper quote with insufficient stock, an eligible but more expensive quote, and an over-budget quote. Offline tests include a real CrewAI kickoff with a local stub LLM, plus approval, price, stock and duplicate-order cases. This proves framework wiring and deterministic rules, not live model quality. Live provider proof and the released Firedrill integration remain pending. The private repository has no license pending publication decisions.
