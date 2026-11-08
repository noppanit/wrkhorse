# Local wrkhorse MCP — implementation handoff for other AI coding models

**Decision date:** 2026-11-07  
**Status:** Architecture selected for implementation; **no real cloud-client → home-Mac MCP call has been verified**.  
**Owner's goal:** Use existing cloud AI chat interfaces (especially Grok Bot and ChatGPT on iPhone) to delegate bounded coding tasks to an **open-weight model running locally on a MacBook Air**. Prefer **no GitHub task relay** and **no Runpod relay/GPU** for home-model requests.

This document is an **implementation brief**, not evidence of a deployed service. Coding agents should read [AGENTS.md](../AGENTS.md) first and work in small reviewable PRs.

## 1. Architecture to implement

```text
         COMPATIBLE CLOUD AI CLIENT
  Grok Bot / Grok custom MCP (verify actual client)
         |    ChatGPT custom MCP: eligible web workspaces only
         |    OpenAI Responses API: optional separate API project
         v
  HTTPS remote MCP interface with client authentication
         |
  Cloudflare Access OAuth-compatible policy (validate interoperability)
         |
  Named Cloudflare Tunnel, OUTBOUND from the Mac (cloudflared)
         |
  127.0.0.1:<wrkhorse-port>/mcp
         |
  wrkhorse MCP server: scope, auth, queue, job IDs, bounded responses
         |
  separate local worker + SQLite (same machine)
         |
  Ollama API on 127.0.0.1:11434
         |
  Qwen2.5-Coder small locally downloaded model
         |
  validated proposed diff -> stored task result -> MCP client
```

**Network location:** `cloudflared`, `wrkhorse serve`, `wrkhorse worker`, Ollama and the SQLite file all run on the **same home MacBook** during the pilot. This avoids a remotely hosted coordinator, networked database, and GPU rental. A sleep/offline Mac cannot process remote jobs.

**Source code** stays in GitHub. **Runtime tasks** use authenticated MCP, not GitHub Issues. Existing `wrkhorse/home_bridge.py` remains a separate fallback proof of concept; do not silently invoke it.

**Public exposure:** only a named, authenticated **MCP gateway** endpoint is reachable through the tunnel. **Never forward Ollama's port 11434** or expose the gateway without authorization. For a Grok-compatible remote MCP test, expect a stable publicly addressable HTTPS hostname; a tunnel alone does not satisfy OAuth/identity requirements.

## 2. What exists today and what does not

| Area | Verified state | Source |
| --- | --- | --- |
| wrkhorse MCP | Four tools implemented and unit-tested via stdio/Streamable HTTP locally; auth token guard present | `wrkhorse/server.py`, `docs/READINESS.md` |
| Queue/worker | SQLite local queue and safe synthetic fixture demo; all on one machine | `wrkhorse/store.py`, `wrkhorse/worker.py`, `make demo` |
| Local model client | An OpenAI-compatible local inference client exists; actual Mac Ollama not yet verified | `wrkhorse/inference.py` (inspect current code), `docs/READINESS.md` |
| GitHub task relay | `wrkhorse/home_bridge.py` committed and unit-tested; no live home execution | `docs/HOME-MACBOOK-PILOT.md` |
| Runpod hosted coding model | Two real Serverless jobs completed; independent of home Mac | `docs/CHATGPT-RUNPOD-PILOT.md` |
| Cloudflare named tunnel / Access policy | **Not configured or tested** | To implement |
| Grok/Grok Bot → home wrkhorse MCP | **Not verified in owner's account or phone** | To test |
| ChatGPT Plus iPhone → custom MCP | **Not currently supported** as direct custom MCP | OpenAI docs below |
| Secure task sandbox / arbitrary repos | **Not implemented** | Do not promise or enable |

Treat model `qwen2.5-coder:1.5b` as the **default candidate** and `qwen2.5-coder:0.5b` as the ultra-small alternative, subject to the Mac's hardware. A model download size is **not** its total RAM requirement. The home Mac's actual hardware and model installation are not known.

## 3. Client compatibility — document facts separately

| Target client | Supported public interfaces | Actual home path status |
| --- | --- | --- |
| **Grok consumer website** | Official custom MCP connector configuration at `grok.com/connectors`; requires reachable URL and authentication | Not tested; test connector auth + tool discovery + `get_project_status` |
| **Grok Bot** | Grok Bot documentation says custom tooling/MCP is supported and its iPhone shares cloud Bot connectors; local desktop execution is another route with explicit permissions | **Not tested in this account**; verify the exact Bot connector configuration on the user's desktop/web first, then mobile reuse |
| **ChatGPT Business / Enterprise / Edu web** | Custom MCP apps supported subject to workspace permissions; public HTTPS endpoint or OpenAI Secure MCP Tunnel | Not configured; **not** the same as Plus iPhone |
| **ChatGPT Plus iPhone** | Custom MCP server creation/use is documented **web only** and full MCP is limited to eligible workspaces | **Blocked as a direct custom MCP route** until product support changes; never claim otherwise |
| **OpenAI Responses API** | Remote MCP `server_url` or private `tunnel_id`, optional approvals/auth | A separate billed API integration; doesn't make existing ChatGPT iPhone chat support the custom MCP |
| **Claude / other apps** | Product/plan-specific; inspect official docs and test before stating compatibility | Unverified |

**Do not equate three different things:** (1) Grok's custom connector on grok.com, (2) Grok Bot's connector/tool setup, and (3) xAI's API MCP tool. Their accounts, authorization and mobile support may differ. Also do not equate ChatGPT subscription with OpenAI API credits.

**Transport choices:**
- **Primary shared multi-vendor route:** named Cloudflare Tunnel + Cloudflare Access or equivalent standards-compatible OAuth + server-level scoped auth. Needs HTTPS reachability for the client. Test whether each client supports the selected OAuth flow; service-token custom headers are **not universally supported** by cloud MCP clients.
- **Alternative OpenAI-only private route:** OpenAI **Secure MCP Tunnel** starts an outbound local process and exposes an OpenAI-managed tunnel ID to supported OpenAI products. Useful where permitted, but is **not a shared endpoint that Grok Bot can use**, and cannot override Plus/iOS product restrictions.
- **Alternative Grok Bot desktop-to-local execution:** Grok Bot desktop may execute an approved local command on the Mac (owner action required). This is **different from the cloud Bot using a remotely callable MCP server**. Keep execution permission `Ask every time` or stricter.

## 4. Engineering tasks in priority order

### T0 — Baseline and inspection (no external service)

- [ ] Inspect `wrkhorse/server.py`, `config.py`, `auth.py`, `worker.py`, `inference.py`, `diffutil.py`, existing tests and `requirements.txt`.
- [ ] Run `make test` and `make demo` locally. Record actual command/result/OS; do not fabricate Mac evidence.
- [ ] Enumerate currently enforced request size, rate, timeout, bearer auth and origin/host behavior; write tests for missing security checks *before* opening an external route.
- [ ] Choose the smallest model that returns a valid patch on this Mac; record exact model tag, chip/RAM and observed latency in a private operational note. Do not put personal or sensitive device details in the public repo.

**Accept:** no regressions; written list of security gaps; no tunnel or paid resource.

### T1 — Mac-only Ollama proof (no cloud connection yet)

- [ ] Install/run Ollama locally. `curl -fsS http://127.0.0.1:11434/api/tags` succeeds.
- [ ] `ollama pull qwen2.5-coder:1.5b` (or `0.5b` if memory constrained). Never choose a `:cloud` tag for this experiment.
- [ ] Configure the **local worker only** for an actual inference mode, retaining the existing fixture/allowlist.
- [ ] Ask for the two-line `sample-add` fix; reject malformed hunks, path traversal, extra files or shell text.
- [ ] Verify the *proposed diff only*. No execution of model-generated code. Prove default worker mode still refuses inference/test execution unless explicitly opted in.

**Accept:** on the actual home Mac, record a real model response and whether the validator accepted it. Local CLI calls work even with no GitHub/Runpod bridge.

### T2 — Harden the remote MCP boundary before exposing it

- [ ] Keep server bound to `127.0.0.1`. If Cloudflare Tunnel points to local port 8765, match Host/Origin handling carefully and test actual reverse-proxy behavior. Avoid simply disabling DNS-rebinding/origin checks.
- [ ] Require authorization **before** tool discovery or invocation; reject missing, malformed and wrong tokens/credentials. A static bearer may work in a controlled local test but is **not automatically client-compatible OAuth**.
- [ ] Assess/implement OAuth resource-server behavior, discovery/metadata and token scopes for compatible remote clients. Verify Cloudflare Access Managed OAuth (with RFC 8707 resource-indicator constraints) **or** an explicitly reviewed external authorization server; do not deploy two conflicting OAuth layers by accident.
- [ ] Bound JSON request/response sizes, concurrency, queue length, timeouts, log data, and result retention. Add stable request IDs and clear `unauthorized`, `unsupported`, `pending`, `diff_ready`, `failed` semantics.
- [ ] Add tests for auth failures, forged Host/Origin, oversized input, duplicate job/replay, timeout, allowlist, path and symlink boundaries. Review prompt injection; tool outputs are untrusted.
- [ ] Separate client permissions; first external tool call should be **read-only `get_project_status`**. Require explicit human approval for `submit_task`; do not add shell/file-write tools in this milestone.

**Accept:** unauthenticated request denied; authorized read-only tool call works on localhost; no secrets in logs, responses or git; a test proves public Ollama port is not exposed.

### T3 — Named Cloudflare Tunnel / secure remote MCP

- [ ] Obtain owner approval and access to **their** Cloudflare account/domain; confirm any DNS/plan cost. Do not invent or purchase a domain.
- [ ] Create a named tunnel and restrict ingress to **one wrkhorse MCP host/path**. Use an Access policy to allow only selected identities or approved OAuth clients. No blanket `bypass` policy, no unauthenticated quick tunnel for a real repo/task.
- [ ] Confirm Cloudflare's OAuth flow works with target MCP client; browser-login redirect alone is often incompatible with non-browser MCP clients. Use supported OAuth/PKCE/resource validation where applicable. Do not assume Cloudflare service-token headers can be configured in every client.
- [ ] Verify outside the LAN that an anonymous `/mcp` call is rejected, an authorized call can discover only intended tools, and `/api/chat` on Ollama is **not** remotely reachable.
- [ ] Document tunnel startup/shutdown and credential rotation. Stopping `cloudflared` must cut off remote access.

**Accept:** stable HTTPS MCP URL, successful authenticated read-only tool call, denied unauthorized calls; no router port forwarded and no exposed raw Ollama API.

### T4 — Prove one Grok client end-to-end

- [ ] **Test Grok Bot first** if the owner's product and account expose custom remote MCP connector setup. Document exact UI/plan and whether its iPhone app can invoke the preconfigured connector.
- [ ] If Grok Bot's available connector flow differs, separately test the documented **Grok consumer custom MCP connector** at `grok.com/connectors`; do not silently treat it as a Grok Bot test.
- [ ] First call `get_project_status`. Then, after owner approval, call `submit_task(task_name="sample-add", mode="inference")`; poll `get_task_status` and retrieve `get_task_result`.
- [ ] Confirm local Mac Ollama logs/task ID match the cloud request, and that the result is a *valid diff* (not executed code). Save redacted tool responses and latency, never tokens or private code.
- [ ] Test home Mac sleep/offline condition, expired token, revoked client, duplicate request and negative input.

**Accept:** one documented *real* cloud client → home Mac → Ollama → client round trip; record which specific product, device, version and permissions worked.

### T5 — OpenAI client path (separate compatibility gate)

- [ ] Recheck OpenAI's official custom-MCP access matrix in the target account and surface before planning integration. Do not assume a Plus subscription on iPhone can create or invoke a custom MCP plugin.
- [ ] If eligible via Business/Enterprise/Edu web, configure the same authenticated public MCP endpoint **or** the OpenAI Secure MCP Tunnel. The OpenAI-only tunnel is an alternative transport, not a Grok shared tunnel.
- [ ] If using OpenAI Responses API, account for separate API pricing and approvals and document that this is not the existing ChatGPT iPhone app.
- [ ] If the owner's account remains ineligible, explicitly mark **blocked**, leave the shared MCP server ready for clients that support it, and suggest an iPhone client with verified remote-MCP support only after testing.

**Accept:** live client-specific call **or** explicit unsupported-product evidence; no false "ChatGPT iPhone connected" claim.

### T6 — Production work (deferred and separately authorized)

- [ ] Before general repo tasks, design disposable sandbox isolation, repo-scoped allowlists, write approval, audit trail and safe branch/PR workflow.
- [ ] Before always-on operation, add process supervision and reboot recovery on the Mac and measure battery/power/network impact. No unattended jobs while asleep.
- [ ] Optional multi-model routing/Claude integration only after per-client compatibility tests.

**No general code execution, repository mutation, new subscription or paid deployment is authorized by this handoff.**

## 5. First development PR: requested scope

A coding agent should pick **T0 + T1** for its first implementation PR: inspect the existing inference client and worker, wire the **existing** sample-only worker to local Ollama under an explicit opt-in, add network-free tests and Mac setup instructions, and leave a reproducing CLI demo. Do **not** start with Cloudflare ingress or OAuth; get the local inference fixture working first.

Proposed PR title: `feat: verify local Ollama sample-add inference behind wrkhorse worker`.

PR description must include:
1. Exact changed files and new tests.
2. `make test` output and whether the **real Mac model** was exercised. CI on GitHub-hosted Linux is *not* a local-model test.
3. Diff correctness and no-model-code-execution evidence.
4. Anything left unverified, especially remote MCP auth and product compatibility.
5. No credentials, hostnames tied to private networks, billing surprises or personal data.

## 6. Configuration questions that must be answered on the home Mac

- What is the MacBook Air chip, memory, macOS version, and Ollama availability?
- Is `qwen2.5-coder:0.5b` adequate for valid diffs, or is `1.5b` necessary?
- Can the owner's **Grok Bot** account configure its own custom remote MCP tool, and can its iPhone app use it? Distinguish from Grok consumer website.
- Does the owner have a domain and Cloudflare account? Is a stable externally reachable hostname acceptable?
- Which clients support the selected OAuth authentication scheme? Do they allow per-client scoping/approvals?
- What is the owner's desired idle policy: Mac awake only during tests, or a supervised always-on workstation?

Do not invent answers. A blocking prerequisite should be surfaced as a specific missing requirement instead of a claim of completion.

## 7. References and existing files

Project files:
- [Agent instructions](../AGENTS.md)
- [README](../README.md) and [readiness](READINESS.md)
- [Existing Runpod proof](CHATGPT-RUNPOD-PILOT.md)
- [Old no-GitHub Runpod-relay proposal](HOME-NO-GITHUB-OPTION.md) — **not the chosen default**
- [Optional GitHub bridge](HOME-MACBOOK-PILOT.md) — **not the chosen default**

Official capability references (recheck if product availability changes):
- OpenAI: [custom MCP servers in ChatGPT](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) — workspace/web availability and limits.
- OpenAI: [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) — outbound-only private transport for eligible OpenAI clients.
- OpenAI: [remote MCP in Responses API](https://developers.openai.com/api/docs/guides/tools-connectors-mcp) — API-specific `server_url`/`tunnel_id`, separate cost and auth.
- xAI: [Grok custom MCP connectors](https://docs.x.ai/grok/connectors) and [Grok connector tunneling](https://docs.x.ai/grok/connectors/custom-mcp-tunneling) — public reachability required for that connector path.
- xAI: [Grok Bot overview](https://docs.x.ai/grok-bot/overview), [Grok Bot mobile](https://docs.x.ai/grok-bot/mobile) and [Grok Bot security](https://docs.x.ai/grok-bot/security) — verify actual account's custom tools, mobile behavior and local approvals.
- Cloudflare: [Managed OAuth](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/) and [Secure MCP servers](https://developers.cloudflare.com/cloudflare-one/access-controls/ai-controls/secure-mcp-servers/) — browser-less auth caveats.

**Outcome we seek:** one measured Grok Bot (if supported) → securely authenticated wrkhorse MCP → MacBook Air Ollama → valid diff → Grok Bot response, **without** GitHub or Runpod in the runtime request path. Other clients may use the same wrkhorse MCP once independently verified.
