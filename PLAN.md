# Purchase request development plan

## Goal
Complete the standalone three-role CrewAI procurement workflow over synthetic requests/quotes, with a separate explicit human decision and a fresh, source-bound final order check. Live semantic/billing proof, qualified new Firedrill and publication/license remain parked.

## Reproduced baseline
At57bed8ec423e320d5a96990d426e17dcc16f74f5 the original4 tests pass. Commit accepted a saved approved proposal changed from20units to1, a selected quote changed to another SKU without revision, and a newly cheaper eligible competitor. Actual native CrewAI/OpenAI accepted3 length-finished role outputs and saved a proposal. Private proof /tmp/firedrill-purchase-baseline.json.

## Tasks
1. Strict bounded source/recommendation/proposal/decision/order/history schemas with exact non-boolean integers/currency/identity/cross-references and deterministic cheapest eligible quote. Bind reviewed request and selected/competing quote snapshots and human decision to the exact immutable proposal. Recheck full facts at commit, preserve changed-source/stock/budget/rejection/duplicates, and serialize cross-process mutations with source/instance fencing. Never infer authentication from a fixture name.
2. Own actual pinned CrewAI native provider calls, private task output storage, original terminal responses/usage, zero retries, explicit request/output/deadline budgets and awaited cancellation/client/thread cleanup with private prepared/partial/failure receipts. Qualify normal CLI over owned HTTP and distinguish injected stubs, normalized SDK observations and unknown counters/cost. No paid call/order/payment.
3. Coherent rich hash-bound starter/busy/ambiguous/boundary request/quote/history populations, key-free exclusive private init/readback/reset preserving original work and source. Keep case/task/scoring labels outside role context.
4. Fresh locked wheel/sdist/complete-source distribution, installed CLI/API/native checks, all cases and full suite, exact-head source/Linux/artifact/catalog proof.

## Current task
Tasks1/2 implemented locally:53 source/transaction/three-role native HTTP/normal CLI/signal/receipt/cleanup checks. Exact source/Linux CI checkpoint next. Tasks3/4 remain active. Overall portfolio goal stays active; continue remaining Wave3 after independent completion.
