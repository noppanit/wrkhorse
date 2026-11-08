# Grok Bot-first local coding pilot (MacBook Air 8GB)

**Decision:** Prioritize a working Grok Bot -> home Mac checkout and small coding test before adding a general multi-client Cloudflare MCP orchestrator.
**Status:** Design and runbook, **not tested on the owner's Mac**. No checkout, local Pi execution, Cloudflare Access login, or Grok Bot-to-home end-to-end call has been verified.

## Why this design

Remote AI orchestration of a local coding worker is a practical pattern; however, a custom MCP gateway may be unnecessary for the **first Grok Bot-only test**. Grok Bot's documented **Execution on this computer** setting lets an approved Bot run commands on the owner's desktop. This is separate from the Grok chat app and from the Bot's default cloud computer.

**Owner hardware:** 8GB unified-memory MacBook Air. Local model candidates: Qwen3.5 4B (test with short context; memory pressure possible), fallback Qwen2.5-Coder 3B or existing repo's Qwen2.5-Coder 1.5B. Download size is not runtime RAM; benchmark actual latency, memory and tool-calling reliability. Do not promise model quality or unattended coding.

## Phase A: simplest Grok Bot -> local Mac (no Cloudflare, no wrkhorse network gateway)

1. Install Grok Bot's **desktop** app on the Mac and sign in. In **Settings -> Computer -> Your computer -> Execution on this computer**, select **Ask every time**, not Always allow. It is per desktop; owner approves the actual commands. The Bot's cloud computer is different.
2. Install Git, Ollama and Pi on the Mac. Pull a locally hosted Qwen model. Configure Pi with Ollama's loopback OpenAI-compatible endpoint `http://127.0.0.1:11434/v1`. Keep Ollama bound to loopback. Start with a small model/context fitting 8GB.
3. Ask Grok Bot, **with per-command approval**, to clone only the allowlisted repository `https://github.com/noppanit/wrkhorse.git` under a disposable folder, e.g. `~/wrkhorse-workspaces/wrkhorse-pilot`, then create an isolated feature branch. Example approved commands:
   ```sh
   mkdir -p "$HOME/wrkhorse-workspaces"
   git clone https://github.com/noppanit/wrkhorse.git "$HOME/wrkhorse-workspaces/wrkhorse-pilot"
   cd "$HOME/wrkhorse-workspaces/wrkhorse-pilot"
   git switch -c pilot/grok-bot-local
   ```
   Checkout is **local to the Mac**: GitHub is only the source repository, **not** a runtime messaging bridge.
4. Ask Bot first to read `AGENTS.md` and propose one small refactor, **read-only**. For the write test, have Pi (through approved local command/tool invocation) edit the disposable clone, run a **known safe test** in a controlled environment, and return `git diff`, exit code and test logs. Pi's tool use is capable of file writes and shell commands; **Pi is not a sandbox**.
5. Review the diff locally. **Do not automatically push, merge, deploy, or use stored credentials.** Never let the Bot execute code from an unknown repository outside genuine OS/container isolation; a new clone, branch, or subprocess alone does not form a sandbox.

**Suggested first user prompt to Grok Bot:** 
> On my Mac (not your cloud computer), with my approval for each command, clone only noppanit/wrkhorse into ~/wrkhorse-workspaces/wrkhorse-pilot. Create a branch. Read AGENTS.md. First report a small refactor plan without editing anything. Do not access other folders, run arbitrary downloaded scripts, access credentials, push, merge, or deploy.

**Acceptance test:** Show the local absolute workspace path, `git status`, which device ran commands, Ollama model ID, Pi model/backend ID, actual diff and test results. Confirm no other local files were accessed. Distinguish Bot cloud execution from local execution.

## Phase B: reusable Grok Bot -> Cloudflare -> wrkhorse -> Pi (later)

For shareable access by multiple *compatible* cloud AI clients, keep one server-owned MCP interface:

```text
Grok Bot / other compatible cloud MCP client
  -> HTTPS + compatible OAuth authorization
  -> Cloudflare named Tunnel + Access (Managed OAuth if interoperable)
  -> wrkhorse /mcp on Mac 127.0.0.1:8765
  -> dedicated scoped job worker / checkout manager
  -> Pi RPC or SDK, using local Ollama / Qwen
  -> branch diff + test result + status (not secrets)
```

- Existing wrkhorse has local MCP, read-only local-model prompt and **sample-add-only** task queue; **it cannot yet clone arbitrary repositories, invoke Pi, run arbitrary tests, or safely edit arbitrary repositories**.
- Add tools such as `prepare_checkout(repo_id, ref)`, `start_coding_task(workspace_id, instruction)`, `get_task_status(task_id)`, `get_diff(task_id)`, `get_test_result(task_id)`, but bind `repo_id` to a server-maintained allowlist and never accept arbitrary URLs, filesystem paths, shell commands, or unreviewed ref names from MCP clients.
- Use separate ephemeral Git worktrees/copies per task, scoped permissions, a true sandbox for untrusted test execution, rate/time/resource limits, diff and code-review gates. Deny push/merge/deploy by default. Protect against symlink/path traversal and prompt injection in repository instructions.
- First verify Grok Bot's actual connector support and its OAuth/RFC8707 compatibility; Grok's consumer custom MCP documentation is a **separate** surface and must not substitute for a real Bot test.
- A named Tunnel/Access app and owner-approved domain/account are prerequisites. Never expose Ollama 11434 or unauthenticated `/mcp`. Cloudflare Access Managed OAuth requires an RFC 8707-compatible OAuth client.
- Existing docs say both Cloudflare Tunnel and the entry-level Zero Trust/Access offering can have a no-charge tier, but plan quotas, Managed OAuth eligibility and any optional hostname/domain cost must be **checked in the actual account before deployment**. An owned domain can avoid new domain purchase; no spend is approved.
- No Runpod or paid inference API is required when using entirely local Qwen. Cloud models still consume tokens/usage; local Qwen does not magically eliminate Grok Bot's own model usage.

**Integration order:** A) Grok Bot native desktop permission and local checkout -> B) Pi + Qwen small refactor -> C) minimal trusted local worker API -> D) Cloudflare OAuth-protected MCP -> E) independent client interoperability tests (Grok Bot first, others later). Stop if auth/tool semantics do not match; do not disable auth to force a demo.

## Official documentation

- Grok Bot local execution / approvals: https://docs.x.ai/grok-bot/settings-and-notifications and https://docs.x.ai/grok-bot/approvals-security-and-privacy
- Grok Bot computers and app connectors: https://docs.x.ai/grok-bot/how-grok-bot-works and https://docs.x.ai/grok-bot/computer-and-apps
- Grok consumer custom MCP and tunnel (distinct product): https://docs.x.ai/grok/connectors and https://docs.x.ai/grok/connectors/custom-mcp-tunneling
- Pi coding agent: https://github.com/badlogic/pi-mono
- Cloudflare Managed OAuth / RFC8707: https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/
- Existing home deployment: [DEPLOY-MACBOOK.md](DEPLOY-MACBOOK.md)
