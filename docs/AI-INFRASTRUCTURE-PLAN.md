# Wrkhorse — Hybrid Autonomous AI Infrastructure Pilot

Status: Proposal / proof of concept (not deployed)
Owner: noppanit
Budget: target $30–$50 GPU experimentation, with manual spend checks; provider spending limits must be verified.

## Goal
Build an always-available task coordination service that accepts requests from existing Claude and Grok apps **where supported**, delegates bounded research and coding tasks to rented GPU inference, and returns traceable results. Validate before purchasing a ~$2,000 home server. Preserve existing mobile interfaces rather than building a replacement.

## Constraints and assumptions
- Claude and Grok consumer subscriptions do not automatically include API usage. Verify each app's ability to connect to a **custom remote MCP server**, authentication requirements, and mobile availability before promising end-to-end delegation.
- MCP is a tool protocol, not a model router or shared memory system.
- Local/open-weight models can handle parts of the workflow but may be weaker at complex coding and reasoning. Compare quality, not just inference cost.
- A rented GPU does not need to run 24/7. Keep a lightweight always-on control plane separate; start/stop GPU workers on demand.
- A GPU rental provider may charge for idle time, attached storage, or stopped instances. Do not assume a hard cost cap unless verified.
- Research must use permitted public sources and retain provenance; a language model alone is not a live web search engine.

## Proposed architecture

```text
Claude app --(supported remote MCP)---+
                                      | HTTPS + auth
Grok app ----(if supported)-----------+--> MCP gateway / control plane
                                               |
                                        task API + queue
                                               |
                              +----------------+----------------+
                              |                                 |
                       research worker                     coding worker
                    fetch/search permitted               isolated repo workspace
                    sources + citations                  edit -> test -> patch/PR
                              |                                 |
                              +---------- GPU inference --------+
                                         (on demand)
                                               |
                                   task state + artifacts
                               GitHub + database/object storage
```

If an app lacks direct MCP access, use a project link or supported alternative integration and mark it as a limitation; do not claim native delegation.

## Pilot stack (replaceable components)
- **Control plane:** small hosted service (e.g. low-cost VM), Python/FastAPI, task queue; HTTPS endpoint with per-client authentication and logs.
- **MCP gateway:** minimal remote MCP server exposing `get_project_status`, `submit_research_task`, `submit_coding_task`, `get_task_result`. Use asynchronous job IDs for long-running tasks.
- **Inference:** rented GPU on RunPod or another provider; vLLM/Ollama depending on model and hardware. Choose a suitable open-weight coding model only after benchmarking GPU memory and costs.
- **Research:** source collectors/search provider or allowed website APIs, document parsing, deduplication, cited findings.
- **Coding:** git worktree or ephemeral sandbox, restricted shell, test runner, diff output; never execute untrusted code on the gateway.
- **State:** start with SQLite + artifact files for a single-node pilot; move to PostgreSQL/object storage if multi-worker concurrency requires it.
- **GitHub:** repo source, task artifacts and reviewable PRs. Never push directly to main.

## Security boundaries
1. Gateway is the only public entry point. Keep GPU inference and databases private.
2. Authenticate each client; use scoped tokens, TLS, request limits, audit logs and secret management.
3. No unrestricted filesystem, shell, or credential access through MCP.
4. Require explicit approval for deployments, destructive actions, purchases and merges.
5. Isolate code execution in disposable containers; apply network and resource restrictions.
6. Store no private rental-property/customer data in public repos; redact secrets from logs.
7. Define task timeouts, retry ceilings, GPU idle shutdown and human review gates.

## Phased implementation

### Phase 0 — verify integration (no GPU rental)
- Confirm Claude and Grok's exact custom MCP support in the **specific iPhone apps and plans**.
- Deploy a tiny HTTPS MCP endpoint and authenticate it.
- Expose read-only `get_project_status` with a fixed test response.
- From each app, attempt a tool call and capture evidence of success/failure.
- **Gate:** at least one app completes the call; document the other app's limitations.

### Phase 1 — end-to-end orchestration
- Add `submit_task`, `get_task_status`, `get_task_result` with durable IDs.
- Implement a bounded queue, retries, logs, and result storage.
- Use a mock worker first to verify completion without GPU spend.
- **Gate:** task survives gateway restart, result is retrievable, unauthorized requests fail.

### Phase 2 — research proof
- Collect a small set of permitted rental-market or startup-market sources.
- Extract claims with URLs, timestamps and confidence/uncertainty.
- Generate a concise report using a rented open-weight model.
- **Gate:** citations resolve; report is useful and factually checked against sources.

### Phase 3 — coding proof
- Select a small scoped MVP feature with acceptance tests.
- Have the worker inspect a disposable repo, implement changes and run tests.
- Produce a diff or draft PR for human review, not an automatic merge.
- **Gate:** tests pass and human review confirms correctness/security.

### Phase 4 — evaluation and hardware decision
- Run comparable research and coding tasks with existing Claude/Grok workflows.
- Record wall time, GPU active/idle hours, rental/storage/search/API costs, model quality, failures, human correction time.
- Decide whether to buy hardware based on actual peak model memory, throughput, uptime needs and break-even analysis.

## Cost controls
- Start with a **$30–$50 experiment budget**, not an assumed provider-enforced hard cap.
- Set provider alerts/limits if available; check pricing and remaining budget before starting GPU instances.
- Auto-terminate GPU resources after inactivity and verify billing stopped.
- Track per-task GPU runtime, API/search charges and storage charges separately.
- Require approval before any paid cloud-model escalation.

## Initial backlog
- [ ] Verify Claude iOS custom remote MCP access
- [ ] Verify Grok iOS custom remote MCP access
- [ ] Choose gateway hosting and authentication
- [ ] Implement read-only status tool and integration tests
- [ ] Add durable task queue and artifact retrieval
- [ ] Benchmark research worker with citations
- [ ] Benchmark coding worker with tests and PR output
- [ ] Produce cost/quality comparison and hardware recommendation

## Definition of success
A user initiates at least one bounded task from an existing supported app; a remote worker executes it; the result can be retrieved in that app without copying files manually. Both a research and coding benchmark complete with acceptable quality, provenance/tests, reviewable artifacts and measured all-in cost. Unsupported app integration is documented honestly.

## Out of scope for the first pilot
Building a new universal chat app; training foundation models; fully autonomous production deployment; purchasing new hardware; guaranteeing compatibility with all consumer apps.
