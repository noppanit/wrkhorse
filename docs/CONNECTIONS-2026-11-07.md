# Connections and ChatGPT coding-task workflow (2026-11-07)

This is a **verified-state log and implementation proposal**, not evidence of a deployed agent. It supersedes only the connection-status claims in older documents; the existing local-pilot safety constraints remain in force.

## User goal

**From a ChatGPT conversation, submit a real coding task to wrkhorse, have an open-weight model hosted on a rented Runpod GPU draft changes, and return the diff / test results / reviewable pull request to ChatGPT.** Keep the current ChatGPT, Claude and Grok interfaces where supported. Do not require a new chat UI.

## Verified connections

| Connection or workload | State on 2026-11-07 | Evidence or limitation |
| --- | --- | --- |
| ChatGPT → GitHub `noppanit/wrkhorse` | **Verified** | GitHub app read repository metadata and README/source/docs; repository granted write permissions. |
| ChatGPT → Runpod management | **Verified** | OAuth-connected Runpod plugin successfully returned `pods: []`, `items: []` (Serverless endpoints), and `networkVolumes: []`. Empty lists count as a successful account-API connection. |
| ChatGPT → **wrkhorse task submission** | **Not connected** | No bridge or custom MCP plugin is configured. The Github and Runpod plugins do not automatically call wrkhorse's four custom tools. |
| wrkhorse → model inference on Runpod | **Not deployed** | No Pod, vLLM service or model API created. |
| Claude / Grok consumer apps → wrkhorse | **Not verified** | No remote MCP registration or live tool call performed from those specific mobile apps. |
| Agent workers executing arbitrary repo code | **Not implemented** | Existing demo only supports `sample-add` and reviewed mock patch; model text is not executed. |

No paid resource was created by this session. The above **does not prove zero historical billing** or a funded Runpod account.

## ChatGPT interaction: two possible transports

### Path A — GitHub issue/task bridge (best fit for current ChatGPT Plus workflow)

```text
User → ChatGPT conversation
         ↓ GitHub app: create a structured issue / approved task request
GitHub issue labeled "wrkhorse-task" (proposed, NOT installed)
         ↓ GitHub Actions validates request and dispatches it
wrkhorse task gateway + durable queue (requires hosted service)
         ↓ bounded coding worker → vLLM on Runpod GPU when started
diff + sandboxed tests (future capability) → PR / issue update
         ↑ ChatGPT uses its existing GitHub app to read result
```

This is an architectural design. **There is no GitHub Actions issue bridge in this repository today.** Create no workflow that executes arbitrary issue-body shell commands. Tasks must be allowlisted, authenticated, size-limited, scoped to the intended repository, and require owner approval for paid GPU startup or code execution. Issue text can be untrusted input; do not expose secrets to issue or PR content. Do not automatically merge into main.

A first **connection-only** test can create a dummy request with an approved marker, trigger a no-network mock handler, record status on the issue, and show that ChatGPT can fetch the result. It does not need a GPU and must not start compute. Subsequent GPU-backed runs require a secured dispatcher, model server, real sandbox, and spending controls.

### Path B — direct wrkhorse MCP tool in ChatGPT (account dependent)

ChatGPT can directly invoke `submit_task`, `get_task_status`, etc. from a registered remote MCP server only on eligible ChatGPT accounts/workspaces. OpenAI's current product guidance says full custom MCP (including write/modify actions) is available to **Business and Enterprise/Edu on web**, governed by workspace permissions; Pro has a restricted read/fetch path, and Plus is not offered full custom MCP creation. Mobile creation/use is not supported in the custom MCP help article.

A future eligible account can connect an authenticated HTTPS `/mcp` endpoint, or an approved Secure MCP Tunnel, via Plugins → + → Add custom MCP server. **No such custom MCP plugin has been created for wrkhorse.** Existing `WRKHORSE_AUTH_TOKEN` bearer authentication also needs validation against the specific client's supported auth configuration.

Sources: https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt and https://developers.openai.com/api/docs/guides/custom-mcp-server .

## Existing wrkhorse constraints that matter

- `wrkhorse serve` and `wrkhorse worker` use the **same local SQLite file**. They cannot run on separate machines as-is.
- Streamable HTTP endpoint exists at `/mcp`, requiring HTTPS exposure and a strong bearer for a remote caller.
- Only `sample-add` mock/inference is accepted; arbitrary GitHub coding requests are not.
- Default `WRKHORSE_EXECUTION_MODE=disabled` refuses execution/model calls. `trusted-local-demo` is **not** a security sandbox.
- The inference path can store a proposed unified diff. It does not execute model-written code or open a PR.
- To achieve the real goal, build a safe bridge, durable shared queue/service, restricted repository workspace, isolated test runner, vLLM connection, and result reporting. Prefer a PR for human review.

## Budget policy — $50 experiment ceiling, NOT a hard cap

Owner's current limit: **$50 total for the initial experiment**. It is a budget instruction, **not yet a provider-enforced spending cap**.

- Do not provision a Pod, CPU server, serverless endpoint, network volume, storage or other paid service solely to verify connections.
- Do not advertise a hard cap or automatic stop unless it is actually installed and tested. The available Runpod connection exposes billing inspection and stop/delete operations but no confirmed cumulative $50 enforcement setting.
- Before any paid deployment, obtain explicit approval of configuration and estimated run/storage expense; confirm actual price, minimum credits, billing, and cancellation path.
- Track measured spending separately for compute, disks, and network volumes; storage may continue charging while GPU is stopped.
- Any budget watchdog must run independently of the resource it stops, reconcile provider billing (which can lag), and include reservation/headroom logic. Merely checking a number after the fact cannot guarantee a hard limit.

Live catalog observed on 2026-11-07 (reference only; stock/prices can change): secure 2-vCPU compute-optimized CPU = `2 × $0.03 = $0.06/hour`, community RTX 4090 24GB = `$0.34/hour`, community A40 48GB = `$0.35/hour`. An uninterrupted 30-day CPU at `$0.06/hour` costs about `$43.20` **before** storage; an always-on CPU plus occasional GPU runs could exceed the $50 allowance. These were not deployed.

Runpod guide: https://agents.runpod.io/ ; product-specific deployment notes: [RUNPOD.md](RUNPOD.md).

## Next acceptance tests (all **pending**, no charge authorized)

1. **ChatGPT → GitHub request:** Create a safe, structured coding-task issue from an explicit user request; ensure the issue includes no secrets. Verify GitHub connector returns the issue.
2. **GitHub → mock wrkhorse:** Add and test the issue-event bridge without Runpod compute; validate signer/allowlist, request size, retries, and status comments.
3. **ChatGPT ← GitHub result:** Have ChatGPT fetch completed dummy task status/diff from the same issue. This proves the end-to-end messaging route, **not** inference.
4. **GPU-backed model:** After operator approves a specific bounded spend and safe execution design, launch vLLM using a compatible GPU/template, run an inference-only coding fixture, and stop compute.
5. **Real coding worker:** Implement disposable isolation, fixed resource and time limits, scoped repo checkout, restricted credentials, reviewed patch/PR, and repeatable tests. Benchmark quality and charges.
6. **Claude/Grok:** Verify their *exact* iPhone apps and subscriptions independently instead of treating Claude Code/Grok Build support as evidence of iOS support.

## Change-log expectations

Update this log and the readiness matrix each time an integration is actually tested. For each test record date, source app/client, tool invocation, success/error, resource IDs with no secrets, billable actions and what remains unverified. Prefer a reviewable pull request over pushing deployment changes directly to `main`.
