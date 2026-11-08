# Readiness

**Mac deployment code is prepared (2026-11-07), including opt-in text-only `ask_local_model` for compatible clients:** [DEPLOY-MACBOOK.md](DEPLOY-MACBOOK.md), `scripts/start-home.sh`, `scripts/probe_home.py`, `tests/test_home_deployment.py`, `wrkhorse/access.py`. CI has network-free tests for strict local Ollama URL/model policy, inference-only sample fixture, Host/Origin safety and Cloudflare Access JWT validation. **No real home Mac model inference or remote cloud client invocation has been performed yet.** A Cloudflare named tunnel, Managed OAuth app, domain and the user's Mac setup are still required for remote access. Generic MCP does not guarantee each consumer app supports a custom connection; the ChatGPT Plus iPhone limitation remains.

**Next implementation target (owner decision):** [shared MacBook Air MCP handoff](LOCAL-MCP-HANDOFF.md). The local Ollama + authenticated remote MCP route for compatible clients is **not yet deployed**. GitHub and Runpod are not preferred as runtime bridges; custom MCP from the existing ChatGPT Plus iPhone app remains product-limited.

Local test evidence comes from this repository's tests and the README. On 2026-11-07 ChatGPT → GitHub and ChatGPT → Runpod **account-level connections were verified**. Two real coding-inference jobs subsequently completed on a Runpod Serverless endpoint, but **this did not invoke the full wrkhorse coordinator**. The MacBook Air Ollama bridge is implemented as a restricted fixture relay but **has not been tested on the actual Mac**. See [CHATGPT-RUNPOD-PILOT.md](CHATGPT-RUNPOD-PILOT.md), [HOME-MACBOOK-PILOT.md](HOME-MACBOOK-PILOT.md) and [CONNECTIONS-2026-11-07.md](CONNECTIONS-2026-11-07.md).

| Component | Status | Evidence |
| --- | --- | --- |
| Requirements plan | Present, not implemented as a whole | `docs/AI-INFRASTRUCTURE-PLAN.md` |
| ChatGPT → GitHub wrkhorse repository | Connected, verified 2026-11-07 | GitHub app read repository files and wrote documentation on a branch; not a wrkhorse task execution bridge |
| ChatGPT → Runpod account | Connected, verified 2026-11-07 | Runpod plugin listed resources and later deployed endpoint `8ypa699kjdzzze`; actual inference calls completed |
| ChatGPT → wrkhorse coding task | **Not connected** | No GitHub issue dispatcher or custom ChatGPT MCP integration yet; see connection log |
| ChatGPT → Runpod coding fixture adapter | **Live model inference succeeded** | `wrkhorse/runpod_pilot.py` and `tests/test_runpod_pilot.py`; two Serverless jobs completed on `8ypa699kjdzzze`, second returned a correctly counted diff; no model-written code executed |
| ChatGPT → MacBook Air Ollama via GitHub relay (legacy alternative) | **Code prepared, live path unverified** | `wrkhorse/home_bridge.py`, `tests/test_home_bridge.py`; requires Ollama and `gh` login on Mac, labeled owner-authored issue, and running `--once`/`--watch`; see home guide |
| Shared remote MCP → home Mac local Ollama (owner's chosen path) | **Local deployment code and auth tests ready; remote path unverified** | New `scripts/start-home.sh`, `scripts/probe_home.py` and `wrkhorse/access.py` with tests; owner must install/run Ollama and Cloudflare Access, and prove actual client calls |
| Four MCP tools, stdio | Ready, tested | `make demo` starts `mcp-stdio` and a worker; `tests/test_pilot.py` `DemoTests` |
| Four MCP tools, Streamable HTTP + bearer | Ready, tested on `127.0.0.1` only | `GatewayTests.test_streamable_http_auth_and_tools` |
| Missing token fails closed | Ready, tested | `ConfigTests.test_serve_exits_when_token_missing` |
| Rate limit and 64 KiB body cap | Partial | In-memory limiter unit-tested; HTTP 429 and the body cap are implemented in `wrkhorse/server.py` and not covered by a separate HTTP test |
| SQLite claim, stale retry, attempt cap | Ready, tested | `StoreTests` |
| Reviewed `sample-add` mock diff and unit test | Ready, tested | `DiffTests.test_reviewed_patch_fixes_fixture`, `WorkerPolicyTests.test_mock_demo_mode_runs_reviewed_test_only`, `make demo` |
| Path and symlink rejection | Ready, tested | `DiffTests.test_path_escape_and_symlink_are_refused` |
| Test child does not inherit secrets | Ready, tested | `DiffTests.test_child_environment_drops_parent_secrets` |
| Default worker refuses execution and model calls | Ready, tested | `WorkerPolicyTests.test_default_mode_refuses_without_running_tests_or_calling_model` |
| OpenAI-compatible client | Partial | Local `127.0.0.1` only. One wall-clock deadline covers connect, headers, and body. Identity bodies are capped at 100,000 bytes. Any other `Content-Encoding` is refused before the body is read (`InferenceClientTests`). No vLLM and no GPU |
| Queue lock and stale attempt | Ready, tested | Separate worker processes share SQLite (`LeaseTests`). A running job's heartbeat recovers after a lock longer than five seconds. A second process reclaims a stale attempt; its artifacts stay under `attempt-<n>` and the first process cannot change them |
| Nonblocking, not in this round | Recorded | HTTP 429 is implemented in the gateway and only unit-tested at the limiter. No extra connector, tunnel, or billing automation |
| Inference result is a diff, not executed code | Ready, tested against a fake diff | `WorkerPolicyTests.test_inference_saves_diff_and_does_not_execute_it` |
| Real isolation (container / gVisor / user namespace) | Not done | No code. Remote execution remains refused |
| Research worker | Not done | No module. `get_project_status` reports `research_worker: not_implemented` |
| Grok Bot / Oddy connector, OAuth, phone apps | Not done | `docs/GROK-BOT.md`. No registration was performed |
| Full wrkhorse coordinator on Runpod, secure gateway, vLLM, budget enforcement | Not done | The working Runpod setup was a separate managed llama.cpp Serverless worker, not the wrkhorse gateway or vLLM; $50 ceiling is not a hard cap |
| Push, pull request, merge, deploy, purchase | Not done on purpose | Worker has no git remote and no payment client |

`make test` and `make demo` do not use a network service. A failure of GitHub Actions on the pull request head is a blocker for merge; do not treat a local pass as a substitute for that check.

## Local commands from this slice

`python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements.txt`, then `make test` (31 tests, exit 0) and `make demo` (exit 0). The demo task was the synthetic `sample-add` fixture. It changed `return left - right` to `return left + right` and the unit test reported `ok` / `OK`. One run wrote `var/demo/artifacts/e2e61abc8322431e9e6316dec57d139f/attempt-1/diff.patch`. That directory is gitignored and a later test run replaces it. No GPU, Runpod, or Grok Bot call was made.
