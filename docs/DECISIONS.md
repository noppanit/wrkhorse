# Decisions

Dated 2026-11-07. The requirements document is [AI-INFRASTRUCTURE-PLAN.md](AI-INFRASTRUCTURE-PLAN.md). This file records the pilot slice only.

## 2026-11-07 — One queue, two processes, four tools

Context: the owner wants a Grok Bot conversation to submit a bounded coding job while a self-hosted model runs on a rented GPU. The plan's research worker, phone apps, and hardware purchase are later phases.

Choice: implement `submit_task`, `get_task_status`, `get_task_result`, and `get_project_status`. `wrkhorse serve` and `wrkhorse mcp-stdio` only touch SQLite. `wrkhorse worker` is a separate process and the only one that may call a model.

Rejected: a single process that runs the job inside the MCP request. Runpod's HTTP proxy closes at 100 seconds, and a client disconnect would have to own the job. Also rejected: a REST-only API. Callers need MCP.

## 2026-11-07 — Official MCP SDK, current class name

Context: the Python SDK 2.3.0 removed `mcp.server.fastmcp.FastMCP`. Importing it raises `ModuleNotFoundError` and tells the caller to use `MCPServer`.

Choice: pin `mcp==2.3.0` and construct `mcp.server.mcpserver.MCPServer`. HTTP uses `streamable_http_app` at `/mcp` with stateless JSON responses. stdio uses `run(transport="stdio")`.

Rejected: pinning `mcp<2` only to keep the FastMCP name. Rejected: hand-rolled JSON-RPC that would drift from the protocol.

## 2026-11-07 — Static bearer, not an OAuth server

Context: the intended remote client sends a static `Authorization` header. This repository does not operate an authorization server.

Choice: an ASGI wrapper in front of the official Streamable HTTP app. The token must be at least 32 characters, must not be the public placeholder, and is compared with `hmac.compare_digest`. Missing or wrong tokens get HTTP 401. stdio has no headers; startup still refuses a missing token, and the trust boundary is the operating-system user on that computer.

Rejected: enabling the SDK's OAuth `AuthSettings` / `token_verifier` pair. That advertises an authorization server and resource metadata this process does not provide. Real OAuth and a real connector install are untested and not done.

## 2026-11-07 — Code execution stays off unless a disposable process opts in

Context: a subprocess is not a sandbox. Remote MCP arguments must not choose a repository path or a test command.

Choice: default `WRKHORSE_EXECUTION_MODE=disabled`. The worker marks the job `refused`, does not spawn a test, and does not call the model. `trusted-local-demo` is set only by `make demo` for its child, or by an operator on a machine that holds no secrets. In that mode the mock task applies the reviewed patch and runs a fixed unit-test loader. Inference mode may request a unified diff, checks the path, and writes it. It does not import or execute the model text.

Rejected: running model-written Python because the unit-test loader would import it. Rejected: hiding that gap in the docs while leaving remote execution on. Container, gVisor, and user-namespace isolation are not implemented.

## 2026-11-07 — SQLite on one machine

Context: the plan allows SQLite for a single-node pilot.

Choice: WAL mode, `BEGIN IMMEDIATE` claims, a fresh claim timestamp while a worker is alive, and a bounded retry only after the claim is stale. Gateway and worker must be given the same `WRKHORSE_DB` path.

Rejected: a network queue or a claim that a laptop gateway and a Runpod worker share a database. They do not, unless the operator mounts one filesystem under both, which this code does not set up.

## 2026-11-07 — Two keys

Context: the gateway and the model server are different trust boundaries. vLLM's `--api-key` does not authenticate every route.

Choice: `WRKHORSE_AUTH_TOKEN` is read by the gateway only. `WRKHORSE_INFERENCE_API_KEY` is read by the worker only. The worker sends it to the configured `/v1` URL and refuses redirects and URLs that embed a secret. The model port is not part of the public MCP URL.

Rejected: one shared bearer for both, and rejected publishing the model server through the Runpod HTTP proxy.

## 2026-11-07 — Lock loss must not drop the lease or finish a newer attempt

Context: a SQLite lock longer than the connection timeout raised `OperationalError` out of the idle loop and out of the heartbeat thread. The claim timestamp then went stale, another worker could take the job, and the first worker could still write a result.

Choice: each store call retries a busy lock at most four times, one second each, with a short backoff. The worker loop and the heartbeat thread catch a remaining busy error and stay alive. `touch` and `finish` include the attempt number, so a worker that lost the claim cannot finish the attempt a later worker owns.

Rejected: an unlimited retry or a multi-minute busy timeout. The task still ends on the test timeout, the inference deadline, or the attempt cap.

## 2026-11-07 — Inference timeout is the whole call

Context: `urlopen(..., timeout=)` stops on socket inactivity. A slow drip keeps the read, and therefore the heartbeat, running past the intended budget.

Choice: a monotonic deadline around connect, write, and each read. The socket timeout is only a short slice so the loop can notice the deadline and close the connection. The read stays on the worker thread.

Rejected: a helper thread per request. A stuck drip must not leave threads behind.
