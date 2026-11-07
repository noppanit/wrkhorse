# Home MacBook Air local-model bridge (ChatGPT → GitHub → MacBook → Ollama)

Status: **code prepared and CI pending; no real MacBook connection has been tested**. Updated 2026-11-07.

## Goal and boundaries

The owner wants to say **in this ChatGPT iPhone conversation**: "Ask my MacBook Air's small local model to fix the sample-add bug", have that prompt reach the **home MacBook Air**, and get back a model-generated diff. This is a **different model and path** from the already verified [Runpod-hosted Qwen model](CHATGPT-RUNPOD-PILOT.md).

```text
ChatGPT (iPhone)
   │ existing GitHub connector (authenticated)
   ▼
GitHub issue in noppanit/wrkhorse (public test fixture only)
   ▲                                       │
   │ result comment                        │ GitHub HTTPS polling (OUTBOUND)
   │                                       ▼
   └──────── wrkhorse.home_bridge running on a MacBook Air
                       │ localhost only
                       ▼
                Ollama :11434
                       │
          Qwen2.5-Coder local model
```

**No port forwarding, public Ollama endpoint, Cloudflare tunnel, SSH access, or Runpod GPU is necessary.** The MacBook makes outbound HTTPS requests to GitHub, and calls its own local model on `127.0.0.1`. ChatGPT talks to GitHub, not directly to the Mac's model API.

This bridge uses the owner-authorized GitHub app for the ChatGPT end, and locally authenticated GitHub CLI (`gh`) for the Mac end. GitHub serves as a durable mailbox. It does not make this specific ChatGPT chat asynchronously receive notifications: after the Mac posts the result, prompt ChatGPT to read the issue/comments or provide the issue link.

**Current security and functionality:** A single allowlisted, public synthetic task (`sample-add`) only. The bridge never executes arbitrary task contents, executes model-written code, pushes commits, opens PRs, merges, accesses other repositories, or exposes local files. It checks the exact issue body, label, repository, and issue author before acting. The model response is validated as a one-file unified diff and posted as text only.

## Step 1 — Install the local model (do these steps on the MacBook)

Requirements: macOS 14+ for current Ollama releases; Python 3.11+, `git`, `gh` (GitHub CLI), a network connection for the first model download. The instructions have **not** been executed on the owner's Mac and its CPU/RAM/chip are unknown.

1. Install Ollama from [official macOS download](https://ollama.com/download/mac) and open the **Ollama** Mac app. Ollama's local API should listen at `http://127.0.0.1:11434`; if the app is not running, launch it. Do **not** set `OLLAMA_HOST=0.0.0.0` or expose port 11434.
2. Choose the smallest local coding model to fit your goals:

   | Model | Published Ollama download | Intended purpose |
   | --- | --- | --- |
   | `qwen2.5-coder:0.5b` | ~398 MB | Absolute smallest/lowest footprint; may fail coding diff quality |
   | `qwen2.5-coder:1.5b` | ~986 MB | **Default** for a more realistic coding proof on MacBook Air |
   | `qwen2.5-coder:3b` | ~1.9 GB | Upgrade if memory and speed allow |

   Source: [Ollama Qwen2.5-Coder catalog](https://ollama.com/library/qwen2.5-coder). Memory used at runtime exceeds file download size. Choose based on your Mac's available unified memory.

3. In Terminal:

```bash
ollama pull qwen2.5-coder:1.5b
ollama list
curl -fsS http://127.0.0.1:11434/api/tags
```

For the **absolute lightest** experiment, replace the first command with `ollama pull qwen2.5-coder:0.5b` and pass `--model qwen2.5-coder:0.5b` in the bridge commands below. This does not use any Runpod credits or a model-provider API key. It uses local electricity and RAM.

## Step 2 — Get wrkhorse and authenticate the Mac to GitHub

```bash
# Install GitHub CLI if needed, e.g. "brew install gh" when Homebrew exists.
gh auth login
gh auth status
git clone https://github.com/noppanit/wrkhorse.git
cd wrkhorse
python3 --version
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests -p 'test_home_bridge.py' -v
```

The GitHub CLI account must be able to read issues and post issue comments in `noppanit/wrkhorse`. Let `gh auth login` handle the sign-in; **never paste a GitHub token into an issue, code file or this chat**. If `gh` is already authenticated, skip login. This repository is currently public, so issue bodies and comments are public.

Create the issue label once (if not already present):

```bash
gh label create wrkhorse-home \
  -R noppanit/wrkhorse \
  --color 0E8A16 \
  --description "Allowlisted home-Ollama sample-add test task"
```

If GitHub reports the label already exists, that is fine. The local relay requires the exact label; adding other labels does not authorize other tasks.

## Step 3 — Prove Ollama works locally without GitHub

```bash
python -m wrkhorse.home_bridge --check
# absolute smallest model, if you pulled it instead:
python -m wrkhorse.home_bridge --check --model qwen2.5-coder:0.5b
```

A useful output is `Local Ollama model returned a valid diff` and a two-line hunk that changes `return left - right` to `return left + right`. A weaker model may respond but produce an invalid diff; that's **model quality failure**, not proof of a ChatGPT connection. This check has **zero GitHub writes**. No proposed code is executed.

## Step 4 — Prove the full path from *this ChatGPT conversation*

1. In ChatGPT, ask: **"Create a GitHub issue in `noppanit/wrkhorse` titled `Home Ollama test: sample-add`, add the `wrkhorse-home` label, and set the body to exactly `WRKHORSE_HOME_V1` on line one and `task: sample-add` on line two."** The issue must be created by your authenticated owner account; the script rejects issues from other authors.
2. On the MacBook (same folder and activated virtual environment), run:

```bash
python -m wrkhorse.home_bridge --once
```

   Or, leave a Terminal open to poll automatically while the Mac is awake:

```bash
python -m wrkhorse.home_bridge --watch --interval 30
```

   Optional explicit task lookup: `python -m wrkhorse.home_bridge --issue NUMBER`. The first eligible issue is processed at most once; a marker in the owner-authored result comment prevents reprocessing. The script does not install a permanent service, background daemon, or login item.

3. The Mac should call **local** Ollama, validate the resulting patch, and post one of `diff_ready`, `invalid_diff`, or `model_unavailable` as a **GitHub issue comment**, containing the marker `<!-- wrkhorse-home-result-v1 -->`.
4. Back here, say **"Check the home Ollama test issue and show me the result."** ChatGPT can use the connected GitHub plugin to read its comments. We then have a measured end-to-end proof, including the actual model tag, issue number and result.

**This is not yet verified end to end.** It needs the MacBook operator to install/start Ollama and run the `--once` or `--watch` command. This chat cannot operate the owner's local Mac, install software there, or attest that the Mac processed an issue without the returned GitHub evidence.

## Other paths (not selected for this pilot)

- **Ollama's ChatGPT Desktop integration**: Ollama's quickstart describes integration with the *ChatGPT desktop app in Codex mode*. This is not equivalent to connecting a remote Mac model to ordinary ChatGPT iPhone chat; it will not satisfy the current phone-to-home requirement. Reference: [Ollama quickstart](https://docs.ollama.com/quickstart).
- **Custom MCP endpoint/tunnel**: a private outbound tunnel from the Mac could expose an authenticated wrkhorse `/mcp` endpoint to compatible clients. ChatGPT custom MCP feature and mobile support are account/workspace dependent; do not assume the current iPhone/Plus environment can invoke it. Tunnel design requires access controls, revocation, HTTPS, auth and safe publishing.
- **Runpod plugin**: manages Runpod account resources and the Runpod-hosted model already tested, but does not automatically reach the MacBook model.

## Safety and operational controls

- **Public repo warning:** the issue body and result comment are public. Only the synthetic sample-add fixture is allowed. Never submit proprietary code, passwords, tokens or customer data. Move to a private repository before considering real work.
- **Fixed task:** GitHub issue content is untrusted input. The script only recognizes exact `WRKHORSE_HOME_V1\ntask: sample-add`, owner author and `wrkhorse-home` label; it ignores free-form commands and paths. An issue isn't permission to execute code or deploy resources.
- **No input-controlled commands:** all GitHub commands are argument-array subprocesses, no `shell=True`; the model endpoint URL is hardcoded to loopback. No command from an issue or model is executed.
- **No arbitrary code execution:** the model output is parsed into a diff, checked against `adder/mathy.py` and the fixture, and only posted as text. Successful `diff_ready` means *syntactically accepted*, not tests passed.
- **No permanent monitoring:** the `--watch` command works only while the Mac and Terminal session are active. Sleep, power loss or internet outage stops polling. Consider `launchd` only after a successful manual round trip and separate owner approval.
- **GitHub API usage:** `--once` is preferred for testing. `--watch` defaults to 30-second intervals; min 10 seconds. Repeated polling can hit API rate limits.
- **No Runpod GPU charges for home route:** Ollama runs locally. The separate Runpod Serverless endpoint `8ypa699kjdzzze` may still exist; the $50 cloud experiment ceiling is a manual policy, not an enforced hard cap. This home test does not stop or delete Runpod resources.
- **Never claim verified:** record the real issue URL, local model tag, validation status and result time only after they are observed. The model weights and macOS version are not verified on the actual MacBook yet.

## Acceptance checklist

- [ ] MacBook Ollama installed; localhost `/api/tags` succeeds
- [ ] Downloaded `qwen2.5-coder:0.5b` or `qwen2.5-coder:1.5b`; recorded exact tag
- [ ] `gh auth status` passes on Mac
- [ ] Local `--check` shows model responded (and whether diff is valid)
- [ ] ChatGPT created a labeled issue in this GitHub repository
- [ ] Mac `--once` or `--watch` posted `wrkhorse-home-result-v1` comment
- [ ] ChatGPT read the comment and accurately displayed the model result
- [ ] No tunnel or public model port; no model-generated code executed
- [ ] Documented issue number, model, timestamps and any reliability/security concerns

Related references: [connections log](CONNECTIONS-2026-11-07.md), [Runpod test](CHATGPT-RUNPOD-PILOT.md), [readiness matrix](READINESS.md).
