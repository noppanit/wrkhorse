# T0 baseline — security limits currently enforced and known gaps

Recorded 2026-11-09 from source inspection and network-free tests (`make test`,
`make demo`, `make demo-inference` on Linux x86-64, Python 3.12). This is the
T0 deliverable from [LOCAL-MCP-HANDOFF.md](LOCAL-MCP-HANDOFF.md): an inventory
of enforced behavior *before* any external route is opened. Not Mac evidence.

## Enforced today (with test names)

| Boundary | Where | Evidence |
| --- | --- | --- |
| Bearer auth required before tool discovery/invocation | `wrkhorse/auth.py`, `wrkhorse/server.py` | `GatewayTests.test_streamable_http_auth_and_tools`, `ConfigTests.test_serve_exits_when_token_missing` |
| Host/Origin checks (DNS-rebinding) | `wrkhorse/server.py` | `GatewayTests` |
| 64 KiB JSON body cap; HTTP 429 in-memory rate limiter | `wrkhorse/server.py` | limiter unit-tested; **no separate HTTP-level test** (see gaps) |
| Task allowlist: only `sample-add`, modes `mock`/`inference` | `wrkhorse/server.py` `submit_task` | rejects any other task name/mode |
| Path allowlist + path/symlink escape refusal | `wrkhorse/diffutil.py` | `DiffTests.test_path_escape_and_symlink_are_refused` |
| Default worker refuses tests + model calls | `wrkhorse/worker.py` | `WorkerPolicyTests.test_default_mode_refuses_without_running_tests_or_calling_model` |
| Inference result is a diff, never executed; tests never run on it | `wrkhorse/worker.py` | `WorkerPolicyTests.test_inference_saves_diff_and_does_not_execute_it` (canary), `DemoTests.test_inference_demo_returns_proposed_diff_without_executing_it` |
| One wall-clock deadline (connect+headers+body); identity-only encoding; 100 KiB response cap; redirects refused | `wrkhorse/inference.py` | `InferenceClientTests` |
| Local-model URL policy: loopback by default, RFC1918/Tailscale needs `WRKHORSE_ALLOW_LAN_MODEL=1`, public/`:cloud` always refused | `wrkhorse/local_model.py`, `wrkhorse/config.py` | `LocalModelTests`, `test_home_deployment.py` |
| inference-only refuses real API keys (dummy key only) | `wrkhorse/config.py` | `load_worker_settings` |
| Model tag must be local (no `:cloud`/`-cloud`) | `wrkhorse/local_model.py` | `validate_model_tag` |
| Job lease, stale retry, attempt cap, artifact path pinning | `wrkhorse/store.py`, `wrkhorse/worker.py` | `LeaseTests`, `StoreTests` |
| Secrets scrubbed from logs (`WRKHORSE_AUTH_TOKEN`, `WRKHORSE_INFERENCE_API_KEY`, GitHub token, vLLM key) | `wrkhorse/logsetup.py` | `LoggingSecretTests` |
| Test child does not inherit parent secrets | `wrkhorse/executor.py` | `DiffTests.test_child_environment_drops_parent_secrets` |
| GitHub drafts: one repo, `posts/drafts/<slug>.md` only, draft PR, opt-in | `wrkhorse/github_drafts.py` | `tests/test_github_drafts*.py` |

## Known gaps (to close in T2, before any remote exposure)

1. **HTTP 429 and the 64 KiB body cap are implemented but only the limiter is
   unit-tested** — no end-to-end HTTP test proves 429/413 on the real gateway.
2. **No duplicate/replay job-ID protection test** at the MCP boundary.
3. **No static bearer is client-compatible OAuth.** Remote clients need an
   OAuth resource-server flow (T2/T3); nothing here satisfies that yet.
4. **Prompt injection is unreviewed**: tool outputs and model text are treated
   as untrusted only by never executing them; no sanitized-preview for the
   human approving a diff.
5. **Real sandbox isolation does not exist** (`subprocess_is_sandbox: false`);
   the mock-mode test runner runs in a plain subprocess on the host.
6. **No queue-length/concurrency cap** on the gateway itself beyond rate
   limiting; a flood of authorized submissions could grow the SQLite queue.
7. **Request-ID audit trail** is partial (job IDs only, no per-request IDs).

## T1 status

The local inference path is implemented and network-free verified: worker
`inference-only` mode calls an OpenAI-compatible loopback endpoint, returns a
validated proposed diff (`diff_ready`), never runs tests on it and never
executes model code. `make demo-inference` reproduces this end-to-end against
a stub loopback model server; `wrkhorse doctor` checks a real Ollama. **No
real Ollama/MacBook run has been performed** — that remains for the owner
(see [DEPLOY-MACBOOK.md](DEPLOY-MACBOOK.md)).
