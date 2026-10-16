# Purchase scenarios

`purchase-request datasets` lists twelve coherent hash-bound cases, each with the business request to focus. Initialize a fresh private state for every case. Scoring expectations remain in tests; role prompts contain only the selected request/quotes and real preceding role outputs.

- Original quotes, explicit currency, and stock substitution compare exact totals, SKU, stock, budget and cheapest eligible quote with lexical ID ties.
- Busy procurement has 20 requests, 80 quotes and four imported approved/order histories. Stock/revisions reconcile against imported allocations. This history supplies no prior model, human-authentication or real supplier-delivery proof.
- Missing currency requires review; hostile supplier data cannot approve an order. Changed price/current quote population invalidate previous reviewed facts.
- Insufficient stock and over-budget cases return needs_review with no invented quote/approval/order.
- One-cent and maximum-safe integer amounts survive actual native SDK recommendations, private proposal, separate synthetic human decision and final order check without floating-point rounding.
- Payment rails and supplier services are absent; all orders are local synthetic records.
- Wrong/changed quantity/SKU/source, altered proposal/decision hash, stock/budget/revision changes, stale approvals, rejected human decision and duplicate commit cannot create a second/unauthorized local order. A stale proposal can still be rejected.
- Actual independent processes serialize commit; one wins and stock decreases once. New dataset IDs fence stale owners; legacy missing IDs lack a cross-reset guarantee.
- Native incomplete/refused/tool/malformed/unknown-argument outputs stop before later roles/proposal. Missing/partial/invalid/zero raw usage is distinct from SDK defaults and unknown cost.
- Cancellation/deadline, recording/source/close failures preserve actual partial effects and private evidence; normal CLI signals drain owned HTTP. The controlled deadline test waits for HTTP or operation settlement with a bound, so cold-start expiry cannot leave a test waiting forever.

Reset materializes the original case at a fresh destination; old state and receipts stay intact. Missing/corrupt authority is rejected without repair. Grade actual persisted proposal, separate decision, order, stock and native trace rather than only final model text.
