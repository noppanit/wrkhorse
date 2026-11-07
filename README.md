# wrkhorse

Local pilot for a queue that a Grok Bot can call, plus a separate worker that can run one reviewed coding fixture. The full product in [docs/AI-INFRASTRUCTURE-PLAN.md](docs/AI-INFRASTRUCTURE-PLAN.md) is still a proposal. This repository does not claim an end-to-end Runpod, Claude, or Grok Bot deployment.

| | |
| --- | --- |
| Ready and tested here | MCP tools `submit_task`, `get_task_status`, `get_task_result`, `get_project_status` over stdio and Streamable HTTP; SQLite queue; reviewed `sample-add` mock diff and unit test |
| Refused by default | Remote code execution. A subprocess is not a sandbox |
| Not done | Real Grok Bot connector, OAuth, phone apps, GPU, vLLM, research worker, merges, deploys, purchases |

Connection setup and budget status (2026-11-07): [docs/CONNECTIONS-2026-11-07.md](docs/CONNECTIONS-2026-11-07.md).

Details: [docs/READINESS.md](docs/READINESS.md), [docs/DECISIONS.md](docs/DECISIONS.md), [docs/GROK-BOT.md](docs/GROK-BOT.md), [docs/RUNPOD.md](docs/RUNPOD.md).

## Quickstart

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
make test
make demo
```

`make test` is the same check GitHub Actions runs. `make demo` (alias `make run`) is an explicit local opt-in: it starts a worker and a stdio gateway as two processes on one SQLite file, queues the synthetic `sample-add` task, applies the reviewed patch, and runs that fixture's unit test. It does not call a model or a network service.

The demo prints a diff. The reviewed change is:

```diff
--- a/adder/mathy.py
+++ b/adder/mathy.py
@@ -1,2 +1,2 @@
 def add(left, right):
-    return left - right
+    return left + right
```

The test output includes `test_two_plus_three` and ends with `OK`. A copy of the diff is left at `var/demo/artifacts/<job-id>/diff.patch` (that directory is gitignored).

## Processes

Gateway, worker, and the SQLite file must be on the **same machine**. This slice has no networked database.

| Process | Command | Reads | Does not do |
| --- | --- | --- | --- |
| HTTP gateway | `python -m wrkhorse serve` | `WRKHORSE_AUTH_TOKEN`, `WRKHORSE_DB` | call a model, run tests, read the inference key |
| stdio gateway | `python -m wrkhorse mcp-stdio` | same token and database | run jobs |
| worker | `python -m wrkhorse worker` | `WRKHORSE_DB`, optional inference settings | read the gateway token |

`serve` binds `127.0.0.1:8765` and exits if the token is missing, shorter than 32 characters, or still the placeholder in `.env.example`. A non-loopback bind also requires `WRKHORSE_ALLOW_PUBLIC_BIND=1` and `WRKHORSE_PUBLIC_HOST`.

The worker's default `WRKHORSE_EXECUTION_MODE` is `disabled`. Queued jobs finish as `refused` and do not start a test process or a model call. `trusted-local-demo` is the only other accepted value. It runs the reviewed mock fixture and, when inference settings exist, asks an OpenAI-compatible server for a diff. Model text is applied only as a file edit inside a temporary directory and is **not executed**.

Copy [.env.example](.env.example) into the process environment or a secret store. Do not commit a real `.env`.

## Limits that are enforced

- Bearer comparison is constant-time. HTTP requests are limited to 60 per minute per client address and 64 KiB bodies.
- `submit_task` accepts only `task_name=sample-add` and `mode=mock|inference`. It has no repository path and no shell argument.
- Diffs must be one unified-diff file edit under `adder/mathy.py`. Symlinks, `..`, and absolute paths are rejected.
- The only test command is a fixed `python -I` unit-test loader with a 20 second timeout, a fresh environment, and no inherited secrets. It runs for the mock fixture only.
- Claims are atomic. A second worker does not take a job that is still inside its claim window. A stale claim can be retried until `WRKHORSE_MAX_ATTEMPTS` (default 2), then the job is `failed`.
- The worker never pushes, opens a pull request, merges, deploys, or purchases anything.
