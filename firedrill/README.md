# Firedrill agent-first integration

The optional toolchain pins released `@firedrill-run/cli` and `@firedrill-run/sdk` **2.2.2** (October 19, 2026), Node 22+. The app retains its locked CrewAI/OpenAI/HTTPX dependencies.

## Prepare and run

From the repository root:

```sh
uv sync --locked --extra test
npm ci --prefix firedrill --ignore-scripts --no-audit --no-fund
npm test --prefix firedrill
node firedrill/node_modules/@firedrill-run/cli/bin/firedrill.mjs validate firedrill/scenario.json --json --non-interactive
```

Validation is offline. In a human terminal, use the same CLI `init` to authorize/select the actual project/environment. Automation uses protected `FIREDRILL_TOKEN`, `FIREDRILL_PROJECT_ID`, `FIREDRILL_ENVIRONMENT_ID`. Model operations independently need `OPENAI_API_KEY` and an enabled `PURCHASE_MODEL` or recipe model parameter. Do not put credentials in recipes.

After scope and provider budget are authorized:

```sh
node firedrill/simulate.mjs --preview
node firedrill/simulate.mjs
```

The bounded sequential recipe invokes the actual purchase-request CLI and normal three-role CrewAI/native OpenAI path. Starter and missing-currency inputs explicitly grant creation of a fictional recommendation proposal. `allowProposal` is a required independent literal caller boolean; only true adds the existing local write grant. No plan is substituted for normal model execution. Busy context inspection uses the real store and is explicitly model-free. Models use `openai/<model>` format. Existing prepared-request, native completion, quote/revision/currency/budget/stock and cancellation checks remain authoritative.

The crew can recommend only. Human approval/rejection, approver identity and purchase-order commitment are not adapter actions. An allowed proposal is not approval or a financial transfer. Existing human decision and current source binding remain separate. Each invocation creates a new private `.runs/firedrill/attempt-*` workspace, first saves outer run/attempt/scenario identity in `invocation.json`, and retains real output/receipts after failure. Earlier state is never reset. Operator/CI credentials are removed from child environments. Current ID-only target invocation excludes controller/assessment data and rejects unsupported Tool bindings. Failure retains actual CLI stdout/stderr/run directory with a nonzero exit; local files are not uploaded platform artifacts. Local cleanup does not prove provider cancellation/billing or rollback. Inspect actual state and receipt after uncertainty; do not blindly retry writes.

## MCP and recovery

Configure remote OAuth operator MCP at **https://mcp.firedrill.run/mcp** using [host setup](https://docs.firedrill.run/guides/coding-agents). MCP prepares, queues and inspects; the local SDK/CLI executes the command target. Host OAuth and execution login are separate. Use `--run BATCH_ID` to attach queued work, `--resume OPERATION_ID` after uncertainty, and `--result BATCH_ID` to read retained results. Preserve SDK journals and local evidence. These scripts retain public SDK diagnostics and whole-batch exit status; a new simulation starts new work.

CI and the fresh locked source archive run offline integration tests with actual procurement context and the native missing-key boundary. They establish wiring and cleanup, not live model quality, platform persistence, or uploaded captures. The observation recipe defines no grader/gate. Authenticated persisted happy/negative runs, paid model quality/cost, publication and licensing remain separate gates.

References: [SDK](https://docs.firedrill.run/sdk/typescript), [command targets](https://docs.firedrill.run/guides/callbacks), [operator MCP](https://docs.firedrill.run/guides/control-mcp), [CI](https://docs.firedrill.run/guides/ci).
