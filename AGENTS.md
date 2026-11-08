# Agent handoff — wrkhorse

This file is the entry point for **Claude, Grok Bot, Codex, ChatGPT, and other coding agents** working in this repository. Start by reading [docs/LOCAL-MCP-HANDOFF.md](docs/LOCAL-MCP-HANDOFF.md), then [README.md](README.md), [docs/READINESS.md](docs/READINESS.md), and [docs/AI-INFRASTRUCTURE-PLAN.md](docs/AI-INFRASTRUCTURE-PLAN.md). Treat live test records as historical evidence, not a promise that an endpoint is still running.

## Current owner decision (2026-11-07)

Build **one locally hosted `wrkhorse` MCP task server** on a **home MacBook Air** and connect compatible cloud AI clients through appropriately authenticated MCP transport. **Ollama and coding-model inference run on the Mac**. The owner does **not** want GitHub Issues or Runpod as the normal runtime message bridge.

A suitable cross-vendor route for clients that accept custom remote MCP:
```text
compatible AI client (e.g. Grok custom MCP)
   -> HTTPS + OAuth / authenticated access
   -> Cloudflare Tunnel (outbound from home Mac; no router port forwarding)
   -> wrkhorse Streamable HTTP MCP server (localhost)
   -> local queue + allowlisted worker -> Ollama (127.0.0.1:11434)
   -> bounded result/diff back via same route
```
This route is **planned, not deployed or verified**.

**Important product blocker:** Custom MCP servers are currently limited to eligible ChatGPT workspaces on **web**, not the owner's existing ChatGPT Plus **iPhone** app. A private tunnel does **not** remove that limitation. For the OpenAI Responses API, Secure MCP Tunnel or a reachable MCP server is a possible separate API-based integration, not equivalent to using the user's ChatGPT subscription. Check current docs and entitlements before building a client-specific adapter.

## Existing evidence, not a home connection

- Existing code: four MCP tools (`submit_task`, `get_task_status`, `get_task_result`, `get_project_status`), local SQLite queue, sample-only `sample-add` fixture, unit tests, and an explicit trusted local demo mode.
- ChatGPT -> Runpod-hosted Qwen inference **worked** on November 7, 2026 via the connected Runpod plugin, but **the wrkhorse gateway and home Mac were NOT in that request path**. See [docs/CHATGPT-RUNPOD-PILOT.md](docs/CHATGPT-RUNPOD-PILOT.md).
- `wrkhorse/home_bridge.py` is an **alternative GitHub Issues relay**, implemented and unit-tested but **not run on the owner's Mac**. Do not use it as the preferred architecture.
- A no-GitHub **Runpod relay** was only a proposal; do not deploy or pay for it to satisfy the current decision. See [docs/HOME-NO-GITHUB-OPTION.md](docs/HOME-NO-GITHUB-OPTION.md).
- **No evidence** that Grok Bot, Grok consumer, Claude consumer, or ChatGPT iPhone has invoked the home's MCP endpoint. The home Mac's chip, memory, Ollama installation, domain, and Access configuration are **unknown**.

## Mac deployment implementation status (2026-11-07)

The repository also has a bounded text-only `ask_local_model(prompt)` MCP tool behind an explicit owner-started opt-in. It calls Ollama on localhost, never executes model output, and is not a general coding agent. The repository now contains [docs/DEPLOY-MACBOOK.md](docs/DEPLOY-MACBOOK.md), `scripts/start-home.sh`, `scripts/probe_home.py`, `wrkhorse/access.py`, and `tests/test_home_deployment.py`. The home startup selects **inference-only** mode, restricts inference to local Ollama, and uses either local bearer auth or verified Cloudflare Access JWT/Managed OAuth. **Only network-free CI has verified this code; the owner's real Mac and cloud-client compatibility are still untested.**

The next agent should **not rebuild the bridge from scratch**. Run the local smoke test on the owner's Mac and inspect actual inference results; then configure a protected named Cloudflare Tunnel and verify a compatible external MCP client with a read-only tool call before enabling even the synthetic task. Access Managed OAuth needs RFC 8707-compatible clients and the owner's real hostname/team-domain/AUD. The current ChatGPT Plus iPhone client cannot invoke a custom MCP endpoint directly under documented availability.

## Agent task order

1. Inspect existing server/auth/config/tests and run `make test` before editing. Do not assume the current bearer-token MCP endpoint already satisfies external client OAuth requirements.
2. Establish a **local-only** Ollama integration and verify `sample-add` produces a **reviewable proposed diff**, without executing model-generated code.
3. Harden the MCP server, auth, rate/size/time limits, origin checks, errors and logs for an **authenticated remote client**. Prove unauthorized callers are rejected; never publish raw Ollama.
4. Create a **secure remote MCP test plan** with named Cloudflare Tunnel plus compatible OAuth/Access mechanism; separately prove Grok (consumer) and Grok Bot support in the actual user's app/account.
5. Run the first real external task only when the owner has installed/configured the home-side tools and approved domain/auth exposure. Record client, request, response, logs and shutdown state.
6. Keep ChatGPT Plus iOS custom-MCP integration marked **blocked** until a *real* supported product path is verified. Do not purchase a new plan, rent a GPU, or invent a magic direct connection.

Full milestones, acceptance criteria, and source links are in [docs/LOCAL-MCP-HANDOFF.md](docs/LOCAL-MCP-HANDOFF.md).

## Safety and collaboration rules

- **Default deny:** the service supports only `sample-add` and known modes. No arbitrary shell, arbitrary paths, client-chosen URLs, repository writes, merges, unattended background jobs, or unsandboxed execution.
- **Never execute model-generated code** in the pilot; a parsed/validated diff is a proposal, not a passing test or a deployed change.
- Use GitHub **only for source code, plans, reviews and evidence** unless the owner explicitly changes the runtime architecture. Do not make GitHub Issues a hidden task queue.
- Do not expose `localhost:11434` or an unauthenticated MCP endpoint publicly. A tunnel is network transport **not authorization**.
- Use short-lived, least-privilege credentials in local secret stores. Never commit or output API keys, tunnel credentials, bearer tokens, real customer code, or private files.
- Keep per-client permissions and approvals, audit request IDs without prompt/secret dumps, and place execution of any untrusted code behind a genuinely isolated sandbox requiring separate approval.
- **No spend without permission**: existing $50 Runpod pilot ceiling is manual, not provider-enforced. This local path needs neither GitHub execution nor Runpod compute.
- Submit a focused PR with tests, explicit evidence, and status of what remains unverified. Update the readiness matrix and handoff doc upon completing a milestone. Do not claim a remote path works after unit tests alone.
