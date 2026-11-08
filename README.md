# wrkhorse

**For coding agents (Codex, Grok Bot, Claude, etc.): start with [AGENTS.md](AGENTS.md) and the [local MCP implementation handoff](docs/LOCAL-MCP-HANDOFF.md).** The preferred future architecture runs Ollama + wrkhorse locally on a MacBook Air and lets compatible cloud AI clients connect over authenticated MCP, without GitHub Issues or Runpod as a runtime bridge. The direct home connection is **not yet tested**; the existing ChatGPT Plus iPhone app cannot currently use custom MCP directly.

Local pilot for safe coding tasks and a future multi-agent system. The [full product plan](docs/AI-INFRASTRUCTURE-PLAN.md) remains a proposal. A **bounded ChatGPT → Runpod Serverless model inference** test has succeeded, but the full `wrkhorse` coordinator, Grok Bot, Claude app, and home MacBook connection have not been verified end to end.

| | |
| --- | --- |
| Ready and tested here | MCP tools `submit_task`, `get_task_status`, `get_task_result`, `get_project_status` over stdio and Streamable HTTP; SQLite queue; reviewed `sample-add` mock diff and unit test |
| Refused by default | Remote code execution. A subprocess is not a sandbox |
| Runpod proof | ChatGPT successfully submitted and completed two inference jobs via its Runpod plugin; not the full wrkhorse coordinator |
| Home Mac proof | An outbound-only GitHub issue → MacBook Ollama bridge is implemented for the sample-add fixture, **not yet tested on the owner's MacBook** |
| Not done | Real Grok Bot connector, mobile MCP, production code sandbox, general coding tasks, research worker |

Connection setup and budget status (2026-11-07): [docs/CONNECTIONS-2026-11-07.md](docs/CONNECTIONS-2026-11-07.md). ChatGPT → Runpod completed coding fixture pilot: [docs/CHATGPT-RUNPOD-PILOT.md](docs/CHATGPT-RUNPOD-PILOT.md). **Home MacBook Air routes:** [GitHub issue bridge](docs/HOME-MACBOOK-PILOT.md) or [no-GitHub Runpod relay + authenticated tunnel (proposal only)](docs/HOME-NO-GITHUB-OPTION.md).

Details: [docs/READINESS.md](docs/READINESS.md), [docs/DECISIONS.md](docs/DECISIONS.md), [docs/GROK-BOT.md](docs/GROK-BOT.md), [docs/RUNPOD.md](docs/RUNPOD.md).

## Deploy the local model on your MacBook Air

**Ready for local installation and testing; not yet tested on the owner's Mac or through a real Grok/Claude/OpenAI cloud client.** Use [the Mac deployment guide](docs/DEPLOY-MACBOOK.md) and the [cross-agent handoff](AGENTS.md).

After installing Ollama, Python 3.12 and Git, the minimal setup on the Mac is:

```bash
git clone https://github.com/noppanit/wrkhorse.git
cd wrkhorse
ollama pull qwen2.5-coder:1.5b
bash scripts/start-home.sh
```

In another Mac terminal: `source .venv/bin/activate && python scripts/probe_home.py --ask "Hello from my Mac"` to ask your **local Ollama model** a text question, or `python scripts/probe_home.py --submit` for the fixed safe coding fixture. It returns a proposed diff; no model-written code is executed or merged.

The gateway exposes a bounded text-only `ask_local_model(prompt)` tool, plus four task-queue tools. A compatible AI agent can ask the local model questions, but it cannot execute commands, edit files or run arbitrary agent workflows. The gateway supports **MCP Streamable HTTP** and two separate auth modes: high-entropy bearer for local/testing or **verified Cloudflare Access JWT** when Cloudflare Managed OAuth is configured with your domain and AUD tag. Never expose the raw Ollama API or the gateway without authenticated access. Different AI apps have different MCP/OAuth support; **the ChatGPT Plus iPhone app does not currently support custom remote MCP connections**. The tunnel does not bypass that product limitation.

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
