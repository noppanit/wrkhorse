# MacBook Air deployment: local Ollama + wrkhorse MCP

**Code prepared for installation; NOT yet exercised on the owner's MacBook or from a cloud AI client.** The purpose is to let **compatible, authenticated MCP clients** ask arbitrary bounded **text-only questions** of an Ollama model **running on your Mac**, as well as submit a safe synthetic coding fixture. This is a real MCP server and a local inference worker—not the GitHub Issues bridge or a Runpod relay.

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
python scripts/probe_home.py --ask "Explain what a Python dictionary is in two sentences."
python scripts/probe_home.py --submit
```

- First command: authenticates to local MCP, lists available tools, and calls read-only `get_project_status`.
- Second command: invokes `ask_local_model` with a bounded text prompt and returns a local Ollama answer, with **no file access, shell or model-written code execution**.
- Third command: explicitly submits `sample-add` in `inference` mode, polls status, then prints the model's proposed diff or a safe failure. The script will not send your local token to any public URL.
- The safe target response changes `return left - right` into `return left + right` in `adder/mathy.py`. It is a **proposal only**; no model-written code gets executed, committed or merged.
- If model quality is insufficient, retry with the 1.5B or 3B local tag; do not assume every output passes validation.

For offline config/CI tests before starting the gateway, run `bash scripts/start-home.sh --check` after installing the model and Ollama.

To check only the model side at any time: `source .venv/bin/activate && python -m wrkhorse doctor` (add `--warm` to load the model into memory). It prints the endpoint, whether it is `loopback` or `lan`, whether the model tag is present, and a concrete hint when something is missing.

## Step B2. Using a model on another machine (optional, for later)

The model does not have to run on the Mac that runs wrkhorse. Any **OpenAI-compatible** server works (Ollama, LM Studio, llama.cpp `llama-server`, `mlx_lm.server`, vLLM), on this machine or on another machine **you control** on the home network or your Tailscale tailnet. The gateway, queue and worker still run together on one machine; only the model moves.

On the **model machine** (example: Ollama on a second Mac or a Linux GPU box):

```bash
# macOS app: launchctl setenv OLLAMA_HOST 0.0.0.0:11434, then restart Ollama
# Linux service: systemctl edit ollama  ->  Environment="OLLAMA_HOST=0.0.0.0:11434"
ollama pull qwen2.5-coder:7b
```

Then **firewall port 11434 to your LAN / tailnet only**: Ollama has no authentication, so `0.0.0.0` exposes it to everyone who can reach that machine. Never forward it on the router. Prefer Tailscale: traffic between machines is encrypted (plain LAN HTTP is not) and the address is stable.

On the **wrkhorse machine**, in `.env` (gitignored) or the environment:

```bash
WRKHORSE_ALLOW_LAN_MODEL=1
WRKHORSE_INFERENCE_BASE_URL=http://gpu-box.your-tailnet.ts.net:11434/v1   # or http://192.168.1.40:11434/v1
WRKHORSE_INFERENCE_MODEL=qwen2.5-coder:7b
bash scripts/start-home.sh
```

What the policy accepts (both gateway and worker enforce it, `wrkhorse doctor` reports it):

| Endpoint | Accepted |
| --- | --- |
| `127.0.0.1` / `::1`, any port | always |
| `10/8`, `172.16/12`, `192.168/16`, Tailscale `100.64/10`, IPv6 ULA `fc00::/7` | only with `WRKHORSE_ALLOW_LAN_MODEL=1` |
| hostnames ending `.local`, `.lan`, `.home.arpa`, `.internal`, `.ts.net` | only with the opt-in **and** only if every resolved address is private; checked at startup |
| `localhost` | refused (may resolve to `::1` while Ollama listens on `127.0.0.1`); use `127.0.0.1` |
| public IPs, other hostnames, `169.254.x.x` (cloud metadata), `0.0.0.0` | always refused |
| Ollama cloud tags `name:cloud`, `name:120b-cloud` | always refused (Ollama forwards them to ollama.com) |

Model servers that require an API key (e.g. vLLM with `--api-key`) are **not** supported in this mode; the worker refuses real inference keys so a cloud key can never be sent by accident.

## Operational notes (things that bite on a laptop)

- **Cold start.** The first request after the model is unloaded reads gigabytes from disk; on a MacBook Air a 7B model can take well over a minute. `start-home.sh` warms the model before accepting requests, and timeouts default to 180 s (worker) / 120 s (`ask_local_model`), max 300 s. Ollama unloads idle models after 5 minutes; set `OLLAMA_KEEP_ALIVE=30m` (or `-1`) on the model machine if remote clients hit cold starts.
- **Reasoning models** (qwen3, deepseek-r1, gpt-oss) emit `<think>…</think>` first. wrkhorse strips it, but they can spend the whole token budget thinking: set `WRKHORSE_INFERENCE_MAX_TOKENS=4096`. A truncated answer is reported in the worker log as `truncated`, not as a vague failure.
- **Context window.** Ollama's default context is small (2–4K tokens) and silently truncates longer prompts. That is fine for `sample-add`; raise `OLLAMA_CONTEXT_LENGTH` on the model machine before adding larger tasks.
- **Small models add prose.** "Here is the diff: ```diff …```" is accepted only when it contains exactly one fenced block; the extracted diff is then validated as strictly as before and only the clean diff is stored.
- **Sleep.** `start-home.sh` runs `caffeinate -i` so the Mac does not idle-sleep while serving (`WRKHORSE_NO_CAFFEINATE=1` to disable). Closing the lid on battery still sleeps the Mac and drops remote clients. If the model is on another machine, that machine must stay awake too.
- **Worker failures are visible.** The start script exits if the worker dies at startup, and worker/gateway logs now say *why* a job failed (`ConnectError`, `truncated`, `not a unified diff`) without logging prompts, model text or tokens.
- **Python.** The start script picks 3.12, 3.13 or 3.11 (in that order); `WRKHORSE_PYTHON=/path/to/python` overrides. Delete `.venv` after a Python upgrade.
- **Behind the tunnel the rate limit is global**, because every request arrives from `cloudflared` on `127.0.0.1`. With one owner that is acceptable; per-identity limits are future work.

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

**MCP URL:** `https://YOUR_HOSTNAME/mcp`. Tools: `get_project_status`, `ask_local_model(prompt)` (text-only, 4 KiB prompt and 16 KiB answer, single concurrent request), `list_files` and `read_file` (read-only text under `WRKHORSE_FILES_ROOT`; disabled with a clear error when that variable is unset), `submit_task(task_name="sample-add",mode="inference")`, `get_task_status(job_id)`, and `get_task_result(job_id)`. The first client call should be read-only. For the first inference test, tell the AI assistant: "Call get_project_status. With my permission, submit sample-add in inference mode, poll its status and show the exact proposed diff. Do not execute or merge code."

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

- **Default worker execution and direct-model chat are disabled.** The home startup intentionally opts into `inference-only` and separately enables text-only `ask_local_model`: it refuses `mode=mock`, never runs tests, never executes the model's proposed code, and accepts only `sample-add`.
- The local inference-only mode refuses public/cloud URLs, cloud-model tags (`:cloud`, `-cloud`) and real model API keys. Non-loopback private-network model hosts need the explicit `WRKHORSE_ALLOW_LAN_MODEL=1` opt-in (see Step B2). Local servers ignore the dummy value `ollama-local`.
- The optional `ask_local_model` MCP tool accepts up to 4096 UTF-8 bytes, returns at most 16 KiB of model text, and permits one in-flight invocation at a time. It uses the same validated local-model URL as the worker and does not execute instructions, commands, files, test runners or model-written code. This is a direct bounded model call (unlike queued coding fixtures); it is not a general coding agent.
- The gateway requires a high-entropy bearer token **before tool discovery** and rate-limits requests. No token, no tools. Token is stored in `var/mcp-token` (gitignored). Use an appropriate token manager when giving credentials to external clients.
- The gateway has a request-body cap and a restricted Host/Origin allowlist; the tunnel's public DNS hostname must be explicitly listed via `WRKHORSE_PUBLIC_HOST`. Cloudflare Access is an **additional protective layer**, not automatically configured by this code.
- No arbitrary file paths, prompt content, repository editing, general command execution, paid GPU allocation, or unattended background scheduling. Queue and worker must stay on the same Mac and share local SQLite.
- Local mode uses a high-entropy bearer; remote Cloudflare Managed OAuth mode verifies Cloudflare Access JWTs and relies on the Access policy for user/client authorization and revocation. The wrkhorse queue still does not enforce separate per-client tool scopes; **do not treat this as a production multi-tenant server**.
- MacBook sleep/power loss breaks remote availability. Stop the startup process and named tunnel when not in use. Rotate bearer token by stopping the server, deleting `var/mcp-token`, restarting and reconfiguring the clients.

## Success criteria and evidence

- [ ] Local Ollama installed and selected model downloaded
- [ ] `bash scripts/start-home.sh` starts gateway and worker without errors
- [ ] `python scripts/probe_home.py` authenticates and calls read-only status
- [ ] `python scripts/probe_home.py --ask "Hello"` returns a local model text answer
- [ ] `python scripts/probe_home.py --submit` produces valid local model diff (or explicitly records failure)
- [ ] Named tunnel is separately deployed **with** authenticated access and a valid external Host
- [ ] Unauthorized external request fails, and raw Ollama endpoint is unreachable externally
- [ ] At least one supported cloud AI client invokes `get_project_status` and then produces an actual Ollama-backed result
- [ ] Actual client/product, model, job ID, timestamps and security verification recorded (never secrets)
- [ ] ChatGPT Plus iPhone custom MCP remains unverified/unsupported until official product capability changes

Existing documentation: [agent handoff](LOCAL-MCP-HANDOFF.md), [readiness](READINESS.md), [Runpod test](CHATGPT-RUNPOD-PILOT.md), [GitHub relay fallback](HOME-MACBOOK-PILOT.md).