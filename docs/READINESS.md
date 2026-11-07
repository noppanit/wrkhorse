# Readiness

Evidence is from this repository's tests and from the commands in the README. Anything that needs a real account, connector, or GPU is **not done**.

| Component | Status | Evidence |
| --- | --- | --- |
| Requirements plan | Present, not implemented as a whole | `docs/AI-INFRASTRUCTURE-PLAN.md` |
| Four MCP tools, stdio | Ready, tested | `make demo` starts `mcp-stdio` and a worker; `tests/test_pilot.py` `DemoTests` |
| Four MCP tools, Streamable HTTP + bearer | Ready, tested on `127.0.0.1` only | `GatewayTests.test_streamable_http_auth_and_tools` |
| Missing token fails closed | Ready, tested | `ConfigTests.test_serve_exits_when_token_missing` |
| Rate limit and 64 KiB body cap | Partial | In-memory limiter unit-tested; HTTP 429 and the body cap are implemented in `wrkhorse/server.py` and not covered by a separate HTTP test |
| SQLite claim, stale retry, attempt cap | Ready, tested | `StoreTests` |
| Reviewed `sample-add` mock diff and unit test | Ready, tested | `DiffTests.test_reviewed_patch_fixes_fixture`, `WorkerPolicyTests.test_mock_demo_mode_runs_reviewed_test_only`, `make demo` |
| Path and symlink rejection | Ready, tested | `DiffTests.test_path_escape_and_symlink_are_refused` |
| Test child does not inherit secrets | Ready, tested | `DiffTests.test_child_environment_drops_parent_secrets` |
| Default worker refuses execution and model calls | Ready, tested | `WorkerPolicyTests.test_default_mode_refuses_without_running_tests_or_calling_model` |
| OpenAI-compatible client | Partial | Local `127.0.0.1` only. One wall-clock deadline covers connect, headers, and body (`InferenceClientTests`: fast drip, slow headers, fragmented success, refused redirect). No vLLM and no GPU |
| Queue lock and stale attempt | Ready, tested | Separate worker processes share SQLite (`LeaseTests`). A running job's heartbeat recovers after a lock longer than five seconds. A second process reclaims a stale attempt; its artifacts stay under `attempt-<n>` and the first process cannot change them |
| Nonblocking, not in this round | Recorded | HTTP 429 is implemented in the gateway and only unit-tested at the limiter. No extra connector, tunnel, or billing automation |
| Inference result is a diff, not executed code | Ready, tested against a fake diff | `WorkerPolicyTests.test_inference_saves_diff_and_does_not_execute_it` |
| Real isolation (container / gVisor / user namespace) | Not done | No code. Remote execution remains refused |
| Research worker | Not done | No module. `get_project_status` reports `research_worker: not_implemented` |
| Grok Bot / Oddy connector, OAuth, phone apps | Not done | `docs/GROK-BOT.md`. No registration was performed |
| Runpod pod, public HTTPS, vLLM, billing | Not done | `docs/RUNPOD.md`. No pod was created |
| Push, pull request, merge, deploy, purchase | Not done on purpose | Worker has no git remote and no payment client |

`make test` and `make demo` do not use a network service. A failure of GitHub Actions on the pull request head is a blocker for merge; do not treat a local pass as a substitute for that check.

## Local commands from this slice

`python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements.txt`, then `make test` (31 tests, exit 0) and `make demo` (exit 0). The demo task was the synthetic `sample-add` fixture. It changed `return left - right` to `return left + right` and the unit test reported `ok` / `OK`. One run wrote `var/demo/artifacts/e2e61abc8322431e9e6316dec57d139f/attempt-1/diff.patch`. That directory is gitignored and a later test run replaces it. No GPU, Runpod, or Grok Bot call was made.
