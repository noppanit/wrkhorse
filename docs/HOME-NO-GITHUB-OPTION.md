# Home MacBook Air connection without a GitHub task bridge

Status: **Architecture proposal only — NOT deployed or tested** (2026-11-07).

This route answers the owner's question: **"Can I ask from the existing ChatGPT iPhone app and have my own MacBook Air run a local model without GitHub moving the tasks?"**

## Short answer

Yes, using an intermediary the current ChatGPT conversation can invoke, plus a secure authenticated route to the Mac. **GitHub is not required for runtime task delivery.** However, ChatGPT's existing Runpod connection is a Runpod account-management/action plugin, **not a direct tunnel into the owner's home network**.

The practical path for the owner's current ChatGPT iOS experience is:

```text
ChatGPT iPhone conversation
   → existing authenticated Runpod plugin: run_endpoint / get_job_status
   → NEW lightweight Runpod Serverless *relay* handler (not the GPU coder)
   → HTTPS request + machine authentication to Cloudflare Access
   → Cloudflare Tunnel (outbound-only process on owner's Mac)
   → NEW allowlisted wrkhorse home task HTTP server (localhost)
   → Ollama local API at 127.0.0.1:11434
   → completion/diff returns along the same connection
   → Runpod job output → ChatGPT conversation
```

**Mac model computation is local**; Runpod handles orchestration and incurs possible modest relay charges. The separate successful Runpod GPU proof used a *different* Serverless endpoint, `8ypa699kjdzzze`, hosting the Qwen model in Runpod's GPU cloud; do not confuse that with this unimplemented relay.

## Component ownership and security

1. **Runpod relay (NEW):** A small Serverless handler, ideally CPU with a zero-worker minimum if an appropriate supported SKU/config is available, accepts only a limited schema (`task_name="sample-add"`, bounded max response size and deadline). It sends an authenticated request to the owner's approved home URL. It must never route arbitrary URLs, accept caller-selected hosts, pass arbitrary shell commands, or perform model-generated code execution. One job at a time, bounded billing. The current Hub llama.cpp endpoint cannot forward requests to a home Mac as-is.
2. **Cloudflare Tunnel (NEW):** `cloudflared` on the Mac makes an outbound encrypted connection. No home-router port forwarding or public origin IP is required. It still creates a network path from Cloudflare to the selected home web service; protect that path with **Cloudflare Access Service Auth** and a short-lived service token. Restrict exposure to the fixed home task endpoint — **never publish Ollama's raw port 11434**. Service token client ID/secret are held in Runpod secrets, never committed or shown in GitHub issues.
3. **Home task server (NEW):** A small locally bound HTTP application receives only authenticated, allowlisted `sample-add` requests; calls Ollama on `http://127.0.0.1:11434` and returns a bounded, validated unified diff. Allow only one fixture and strict time/size limits in the first test. No general filesystem, shell, private keys, or remote-execution routes. A tunnel by itself doesn't add the task server.
4. **MacBook Ollama:** Install and start Ollama; initial model `qwen2.5-coder:1.5b`, with `qwen2.5-coder:0.5b` available for the smallest memory footprint. Validate local output before exposing a private task endpoint. Model stays local.
5. **ChatGPT tool invocation:** Invoke the Runpod Serverless relay through the **existing connected Runpod plugin**. ChatGPT submits a job, polls status if queued, then displays returned patch. It does not need to be on the Mac's LAN and it does not call GitHub to transfer tasks.

## What still needs to be built

- A safe home HTTP handler for the fixture, with locally enforceable validation, timeouts, request IDs, response size limits, and explicit authentication. Existing `wrkhorse/home_bridge.py` is a **GitHub polling client**, not an HTTP server.
- Cloudflare account/tunnel and Access policy, a stable secure hostname, and service-token provisioning. Check Cloudflare plan costs and the user-approved hostname. Never put a Cloudflare secret into GitHub or this conversation.
- A minimal Runpod Serverless relay image/handler and secrets. Confirm the CPU or minimum-resource Serverless options and measured price before creating anything billable. Never reuse the existing Qwen GPU inference worker as a security proxy without adding server-side relay functionality.
- A proof using one synthetic `sample-add` request from ChatGPT to the Runpod relay, to the Mac's **local Ollama** and back. Collect remote endpoint/job IDs and local logs; the home connection must be explicitly shown as *unverified* until this completes.
- An explicit shutdown plan for the new relay and budget checks. The historical $50 Runpod experiment ceiling is **not** a provider-enforced hard cap. Verify billing reconciliation; scale-to-zero is not a guarantee of no charges.

## Comparison of no-GitHub alternatives

| Path | Works from current ChatGPT iPhone chat? | Runtime intermediary | Main limitation |
| --- | --- | --- | --- |
| ChatGPT → Runpod plugin → secured Cloudflare Tunnel → Mac | **Plausible with additional code**, not tested | New Runpod Serverless relay + Cloudflare | Must build a safe relay and auth; Runpod charges possible |
| ChatGPT → custom wrkhorse MCP via Secure MCP Tunnel | **Not currently available in this Plus/iPhone workflow** | OpenAI-hosted MCP tunnel | OpenAI docs limit full custom MCP to Business/Enterprise/Edu, web only |
| Other iPhone UI → VPN/Tailscale → Ollama | Depends on chosen client, **not this ChatGPT chat** | VPN/private network | Requires a compatible app or a separate interface |
| ChatGPT desktop Codex/Ollama integration | Local desktop workflow, **not proof of iPhone chat** | Local desktop tooling | Doesn't provide an iPhone-to-home custom tool on current ChatGPT plan |

**Avoid mislabeling:** a tunnel alone transports HTTP. It does not authorize a ChatGPT tool, create an agent, perform task scheduling, or provide local code sandboxing.

## Suggested acceptance checklist

- [ ] Local Mac Ollama API works on loopback.
- [ ] Small fixture task server rejects unauthorized/unknown tasks and anything that looks like a command or URL.
- [ ] Cloudflare Tunnel and Access policy require machine authentication; verify anonymous 401/403 and authenticated 200 without exposing Ollama.
- [ ] New Runpod relay is documented with its container image, endpoint ID, worker limits, timeout, budget plan, and credentials only in a secret store.
- [ ] ChatGPT invokes the relay via the already connected Runpod plugin.
- [ ] Mac log shows actual Ollama inference matching the submitted job ID.
- [ ] ChatGPT receives the local-model diff. Confirm patch is syntactically valid; do not execute or merge model-written code.
- [ ] Worker returns to zero active billable jobs; reconcile reported costs after billing posts.
- [ ] Record success/error evidence and cleanly close all security exposures when not using the home bridge.

## Official references (verified 2026-11-07)

- [Cloudflare Tunnel fundamentals](https://developers.cloudflare.com/tunnel/concepts/) — outbound connections, no inbound router port.
- [Cloudflare Access service tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/) — service authentication with `CF-Access-Client-Id` and `CF-Access-Client-Secret`.
- [ChatGPT custom MCP availability](https://help.openai.com/en/articles/12584461-connecting-custom-mcp-servers-in-chatgpt) — web-only, full custom MCP restricted to eligible workspaces.
- [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) — a separate approach for supported custom MCP workflows.

See also [home GitHub-based pilot](HOME-MACBOOK-PILOT.md) and [working Runpod-cloud model test](CHATGPT-RUNPOD-PILOT.md).
