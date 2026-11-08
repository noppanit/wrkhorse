# MacBook Air deployment: local Ollama + wrkhorse MCP

**Code prepared for installation; NOT yet exercised on the owner's MacBook or from a cloud AI client.** The purpose of this deployment is to let **compatible, authenticated MCP clients** submit a safe synthetic coding task to an Ollama model **running on your Mac**. This is a real MCP server and a local inference worker—not the GitHub Issues bridge or a Runpod relay.

## The architecture

```text
Compatible cloud MCP client (e.g. Grok custom connector, when auth works)
    -> HTTPS, supported identity/OAuth or compatible bearer auth
    -> optional Cloudflare Access + named Cloudflare Tunnel (outbound from Mac)
    -> http://127.0.0.1:8765/mcp (wrkhorse; authenticated MCP gateway)
    -> SQLite queue + separate wrkhorse worker (same Mac)
    -> http://127.0.0.1:11434/v1 (Ollama; local-only)
    -> Qwen2.5-Coder locally downloaded model
    -> proposed one-file unified diff -> queue -> MCP client
```

**No Github runtime bridge, Runpod relay, cloud-model subscription or port forwarding.** A tunnel only solves network reachability. Compatible authentication and individual client support must be established before a cloud client can call this endpoint.

## Step A. Install locally on the Mac (about 3 commands after prerequisites)

1. Install and open the official [Ollama macOS app](https://ollama.com/download/mac). The local Ollama API must be running at `http://127.0.0.1:11434`. **Never** expose port `11434` on your router, LAN or tunnel.
2. Install **Python 3.12** (Homebrew: `brew install python@3.12`; otherwise use an official Python 3.12 distribution), and `git`.
3. Run:

```bash
git clone https://github.com/noppanit/wrkhorse.git
cd wrkhorse
ollama pull qwen2.5-coder:1.5b
bash scripts/start-home.sh
```

The startup script checks Ollama, builds a local `.venv` if necessary, installs pinned Python dependencies, generates `var/mcp-token` with restrictive local permissions, starts the worker and MCP gateway, and runs until you press **Ctrl-C**. No service is installed to start after reboot and nothing automatically runs when the Mac is asleep.

**For the smallest model** (~398 MB download), substitute:

```bash
ollama pull qwen2.5-coder:0.5b
WRKHORSE_INFERENCE_MODEL=qwen2.5-coder:0.5b bash scripts/start-home.sh
```

The default 1.5B tag is ~986 MB on disk. Actual runtime RAM usage may be significantly larger. Smaller models can produce malformed diffs; the worker rejects invalid patches instead of executing them.

## Step B. Test it entirely on the Mac first

Keep the startup Terminal open. In a **second Terminal**:

```bash
cd wrkhorse
source .venv/bin/activate
python scripts/probe_home.py
python scripts/probe_home.py --submit
```

- First command: authenticates to the local MCP endpoint, lists its four tools, and calls the read-only `get_project_status`.
- Second command: explicitly submits `sample-add` in `inference` mode, polls status, then prints the model's proposed diff or a safe failure. The script will not send your local token to any public URL.
- The safe target response changes `return left - right` into `return left + right` in `adder/mathy.py`. It is a **proposal only**; no model-written code gets executed, committed or merged.
- If model quality is insufficient, retry with the 1.5B or 3B local tag; do not assume every output passes validation.

For offline config/CI tests before starting the gateway, run `bash scripts/start-home.sh --check` after installing the model and Ollama.

## Step C. Secure remote MCP access (requires your Cloudflare account)

**This repo now supports Cloudflare Access Managed OAuth assertion verification.** The HTTP gateway can use either (1) a local static bearer for loopback tests **or** (2) a **verified Cloudflare Access JWT** when the Access configuration is set. There is **no fallback to the local bearer in Access mode**. The Cloudflare OAuth flow is performed by Cloudflare Access, not by the wrkhorse application. This still requires the external MCP client to support Cloudflare's OAuth flow and **RFC 8707 resource indicators**.

1. **Create a named Cloudflare Tunnel and hostname** using an existing domain you control. Install `cloudflared`, create the tunnel and DNS route following [Cloudflare's official setup](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-local-tunnel/). Copy [the sample ingress config](../deploy/cloudflared-config.example.yml) to a file outside this repo, replace the hostname/UUID/credential-file, and keep those credentials private. The ingress permits only `/mcp` to `http://127.0.0.1:8765`; unmatched paths return 404. It **must never forward port 11434 (Ollama)**.
2. **Protect the hostname with a Cloudflare Access application** and restrict it to your user identities. Enable **Managed OAuth** under the application's advanced settings and choose allowed client redirect URIs according to the target client's documentation. A custom cloud MCP client must support RFC 8707; browser-only Access redirects without Managed OAuth are not sufficient. [Cloudflare Managed OAuth docs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/).
3. From the Access dashboard, obtain the application **Audience (AUD) tag** and your **Cloudflare Access team domain**, typically `your-team.cloudflareaccess.com`. These are configuration metadata, **not a secret token**. The gateway downloads signing keys only from `https://YOUR_TEAM.cloudflareaccess.com/cdn-cgi/access/certs` and validates the forwarded `Cf-Access-Jwt-Assertion`: **RS256 signature, issuer, audience, issued-at and expiration**. Key/verification/network failures deny access; the JWT is never logged.
4. Stop the local bootstrap if it is running. Then start the home gateway in OAuth/JWT mode with your real values:

```bash
WRKHORSE_PUBLIC_HOST=agent.example.com \
WRKHORSE_ACCESS_TEAM_DOMAIN=your-team.cloudflareaccess.com \
WRKHORSE_ACCESS_AUDIENCE=REPLACE_WITH_YOUR_REAL_APP_AUD \
bash scripts/start-home.sh
```

   The hostname `agent.example.com` is **illustrative**. The listener still binds to `127.0.0.1`. The MCP SDK validates the external Host/Origin. In Access mode the locally generated bearer token is **not accepted**: `scripts/probe_home.py` is intentionally a *pre-Access*, local-auth probe. A remote connection must go through your authenticated Cloudflare application.
5. Run your private `cloudflared` named tunnel, then test from **outside** the home network. Verify unauthorized requests receive 401/403, an authenticated client's `get_project_status` succeeds, and `https://YOUR_HOSTNAME/api/chat` and any path other than `/mcp` are blocked. Only then permit the synthetic `submit_task` tool.
6. To revoke remote access, **stop the tunnel** and disable/revoke the Access application/client sessions as needed. To restore local bearer testing, stop the server and restart **without** `WRKHORSE_ACCESS_TEAM_DOMAIN` and `WRKHORSE_ACCESS_AUDIENCE` (and without the tunnel).

**MCP URL:** `https://YOUR_HOSTNAME/mcp`. Tools: `get_project_status`, `submit_task(task_name="sample-add",mode="inference")`, `get_task_status(job_id)`, and `get_task_result(job_id)`. The first client call should be read-only. For the first inference test, tell the AI assistant: "Call get_project_status. With my permission, submit sample-add in inference mode, poll its status and show the exact proposed diff. Do not execute or merge code."

**Compatibility warning:** Some clients support static bearer credentials but not OAuth; others support OAuth but not the required RFC 8707 flow. They cannot all be made compatible with one tunnel setting. Where a client cannot authenticate, do **not** disable the Access policy or expose the raw gateway; record the limitation and build/test a client-specific standards-compliant adapter separately.

### Suggested client setup order

| Interface | What can be configured | Limitation |
| --- | --- | --- |
| A local MCP client | `http://127.0.0.1:8765/mcp` + bearer from local `var/mcp-token` | Test directly on Mac first |
| Grok website custom connector | Add secure `https://YOUR_HOSTNAME/mcp` through [Grok Connectors](https://docs.x.ai/grok/connectors) | Authentication flow must be supported; actual account not tested |
| Grok Bot | Use custom MCP / desktop tooling as supported in your Bot product | **Different from Grok website connector**; check actual iPhone and account behavior |
| Claude or other MCP clients | Can use the same MCP protocol server if their plan/UI supports remote MCP and chosen auth | Each client needs a separate compatibility test |
| ChatGPT Business/Enterprise/Edu on web | Eligible workspace members can register custom remote MCP, with admin settings | Not the owner's current Plus iPhone client |
| ChatGPT Plus **iPhone app** | **Not a supported custom MCP registration path** in current product docs | No server or tunnel change can make this work directly |
| OpenAI Responses API | Can call remote MCP from custom code | Separate API costs, authentication and client app; not this ChatGPT subscription |

**Do not advertise "works with every AI app".** MCP is the common protocol; product support, OAuth flows, permissions, and mobile availability are separate compatibility gates. A bearer-token-only remote endpoint is **not** automatically compatible with providers requiring OAuth discovery/dynamic client registration.

## Security and limitations

- **Default worker execution is disabled.** This startup intentionally opts into `inference-only`: it refuses `mode=mock`, never runs tests, never executes the model's proposed code, and accepts only `sample-add`.
- The local inference-only mode refuses non-loopback URLs, cloud-model tags and real model API keys. Ollama's local OpenAI compatibility ignores the dummy value `ollama-local`.
- The gateway requires a high-entropy bearer token **before tool discovery** and rate-limits requests. No token, no tools. Token is stored in `var/mcp-token` (gitignored). Use an appropriate token manager when giving credentials to external clients.
- The gateway has a request-body cap and a restricted Host/Origin allowlist; the tunnel's public DNS hostname must be explicitly listed via `WRKHORSE_PUBLIC_HOST`. Cloudflare Access is an **additional protective layer**, not automatically configured by this code.
- No arbitrary file paths, prompt content, repository editing, general command execution, paid GPU allocation, or unattended background scheduling. Queue and worker must stay on the same Mac and share local SQLite.
- Local mode uses a high-entropy bearer; remote Cloudflare Managed OAuth mode verifies Cloudflare Access JWTs and relies on the Access policy for user/client authorization and revocation. The wrkhorse queue still does not enforce separate per-client tool scopes; **do not treat this as a production multi-tenant server**.
- MacBook sleep/power loss breaks remote availability. Stop the startup process and named tunnel when not in use. Rotate bearer token by stopping the server, deleting `var/mcp-token`, restarting and reconfiguring the clients.

## Success criteria and evidence

- [ ] Local Ollama installed and selected model downloaded
- [ ] `bash scripts/start-home.sh` starts gateway and worker without errors
- [ ] `python scripts/probe_home.py` authenticates and calls read-only status
- [ ] `python scripts/probe_home.py --submit` produces valid local model diff (or explicitly records failure)
- [ ] Named tunnel is separately deployed **with** authenticated access and a valid external Host
- [ ] Unauthorized external request fails, and raw Ollama endpoint is unreachable externally
- [ ] At least one supported cloud AI client invokes `get_project_status` and then produces an actual Ollama-backed result
- [ ] Actual client/product, model, job ID, timestamps and security verification recorded (never secrets)
- [ ] ChatGPT Plus iPhone custom MCP remains unverified/unsupported until official product capability changes

Existing documentation: [agent handoff](LOCAL-MCP-HANDOFF.md), [readiness](READINESS.md), [Runpod test](CHATGPT-RUNPOD-PILOT.md), [GitHub relay fallback](HOME-MACBOOK-PILOT.md).