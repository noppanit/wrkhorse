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

## Step C. Secure remote MCP access (requires your own setup)

**Do not expose the server until authentication is proven.** You can use one **named Cloudflare Tunnel** for compatible external MCP clients; Cloudflare Tunnel creates an outbound connection so you do not need router port forwarding.

1. Obtain/control a domain and Cloudflare account. Install `cloudflared` on the Mac. Create a named tunnel and DNS hostname (see [Cloudflare's documentation](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/get-started/create-local-tunnel/)). Do not commit the tunnel credentials.
2. Secure the MCP hostname with an appropriate **Cloudflare Access policy and OAuth or client authentication flow**. Test that an unauthorized request is denied *before* linking a cloud AI client. The application itself also requires a persistent bearer token in `var/mcp-token`. Your client must support the chosen authentication method: OAuth-based MCP clients cannot automatically use an arbitrary static bearer token; a compatible authorization gateway may be required.
3. Configure the tunnel's only route to **`http://127.0.0.1:8765`**, not Ollama. Use the [example tunnel config](../deploy/cloudflared-config.example.yml) only after editing the placeholders locally. Restrict the ingress to the MCP path, return 404 for unmatched requests, and ensure the external `Host` is preserved or explicitly forwarded. **Do not use an unauthenticated quick tunnel** for live tasks.
4. Tell the local gateway its public hostname to make the MCP SDK accept *only* the expected external `Host` and `Origin`:

```bash
WRKHORSE_PUBLIC_HOST=agent.example.com bash scripts/start-home.sh
```

   Replace `agent.example.com` with your actual hostname; this is not a real deployment address. The gateway still binds `127.0.0.1` only.
5. Run a read-only request from outside your home network using a **client that explicitly supports your configured authentication**. First call `get_project_status`. Only then submit the synthetic task. Log the client/tool, model tag, job ID and result. Do not copy auth tokens or private code into the public GitHub repository.

**MCP endpoint:** `https://YOUR_HOSTNAME/mcp`. The tool surface is `get_project_status`, `submit_task(task_name="sample-add",mode="inference")`, `get_task_status(job_id)`, and `get_task_result(job_id)`.

For the first remote test, give the AI client this instruction: **"Call get_project_status on wrkhorse. Then, with my permission, submit sample-add in inference mode, poll status and show me the exact proposed diff. Do not run or merge code."**

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
- The prototype bearer is shared across authorized clients, with no per-client OAuth scopes or dynamic credential revocation. **Do not treat this as a production multi-tenant server.** A production-ready OAuth authorization layer is follow-up work.
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