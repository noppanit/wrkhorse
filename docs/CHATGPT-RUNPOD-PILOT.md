# ChatGPT → Runpod model coding pilot

## Verified live result: 2026-11-07

**Important connection boundary:** The successful test was **ChatGPT → connected Runpod management/action plugin → Runpod Serverless llama.cpp worker → Qwen2.5-Coder-7B model → ChatGPT**. This is **NOT** ChatGPT talking directly to a local model on the owner's home machine. It is also **NOT** a remote invocation of the `wrkhorse serve` gateway or `wrkhorse worker` agent runtime. Those connections and workloads are separate milestones.

- GitHub [PR #4](https://github.com/noppanit/wrkhorse/pull/4) was merged into `main` as commit `70e3dddb260e39e13785f779b10b2ce35d40d975`, after the latest Actions CI passed.
- After the owner funded Runpod, the Hub endpoint was created successfully: `8ypa699kjdzzze`, named `wrkhorse-coder-pilot-20261107`; release `Jacob-ML/inference-worker` `v1.2.3`.
- Confirmed endpoint configuration: `workers.min=0`, `workers.max=1`, `workers.idleTimeout=1`, `timeout=180000` ms, pool `ADA_24`, model argument alias `wrkhorse-coder`. The actual workers reported NVIDIA GeForce RTX 4090 GPUs.
- **Job 1:** `d4bfaf64-134d-4f54-b4db-5e792d3b3f7e-u2`; `COMPLETED`, reported `executionTime=964` ms, `delayTime=83729` ms. Model generated the correct edit but put `@@ -1,3 +1,3 @@` on a two-line source, so the raw hunk counts were invalid. Record this as an *inference success but diff-validation failure*.
- **Job 2:** `e08331db-b8ac-419b-a037-102cdd3bd3d6-u1`; `COMPLETED`, reported `executionTime=794` ms, `delayTime=22914` ms. The model returned a syntactically consistent two-line unified diff, `@@ -1,2 +1,2 @@`, replacing `return left - right` with `return left + right`. It was manually inspected against the fixture; **the code was not executed or merged** and the proposed validator was not invoked remotely on the response.
- After both jobs, Runpod reported no queued/in-progress tasks. The worker list showed three `IDLE` entries, zero `RUNNING`. Provider documentation describes `IDLE` as scaled down/not billed in the console, but status and billing should be reconciled with a settled invoice. Despite `workers.max=1`, the worker history listed three idle records, so don't infer the maximum from the number of historical/idle records.
- At the last billing query, Runpod's endpoint-specific and aggregate daily billing returned zero recorded charges/records. **This is reporting lag or no posted usage, not proof the two launches cost $0.** The $50 ceiling is still **not a provider-enforced hard cap**.
- The endpoint has been **retained** for future explicitly requested tasks with min workers 0; it was not deleted. Re-check endpoint settings/active worker count and budget before another paid run. No continuous monitoring is configured.

The integration proves that prompts sent from this ChatGPT conversation can reach the rented Runpod model and return code suggestions. It does **not** prove that an arbitrary coding task can be safely executed, tested, or committed by wrkhorse; those remain future work.

Updated: 2026-11-07. This is a pilot of **model-generated diffs**, not an autonomous repo editor.

## What the user should be able to say

> ChatGPT, ask the wrkhorse model on Runpod to fix the sample-add bug. Show the exact diff; do not run code or merge.

Intended call flow:

```text
ChatGPT conversation
   → authenticated Runpod plugin / run_endpoint or runsync_endpoint
   → one scale-to-zero Serverless llama.cpp GPU worker
   → private, downloaded Qwen2.5-Coder model (open-weight)
   → OpenAI-shaped answer with one unified diff
   → ChatGPT validates the proposal against wrkhorse's fixture
   → ChatGPT displays the diff; human decides whether to apply
```

ChatGPT has a Runpod plugin with `run_endpoint`, `runsync_endpoint` and `get_job_status`. **No additional ChatGPT custom MCP connection is necessary for this route.** That does **not** imply ChatGPT is directly invoking the custom `wrkhorse serve` MCP gateway or that the existing `wrkhorse worker` process is running on Runpod. The local Python helpers in [../wrkhorse/runpod_pilot.py](../wrkhorse/runpod_pilot.py) define the request and validation contract. The user can later choose to extend the contract to the full queue/worker if desired.

This first pilot intentionally accepts only the reviewed `sample-add` fixture. General repo editing, test execution, sandboxing, submitting PRs, and arbitrary shell commands are **not** offered.

## Historical pre-funding setup status (superseded by live result above)

| Component | Result |
| --- | --- |
| ChatGPT → GitHub `noppanit/wrkhorse` | Connected; read/write verified |
| ChatGPT → Runpod management plugin | Connected; Pod/endpoint/volume listing succeeded |
| Account resource inventory | No Pods, Serverless endpoints, or network volumes as observed during setup |
| Daily Runpod billing | Current-day query reported $0 recorded spend at setup; not a promise against subsequent usage or billing delays |
| Model deployment attempt | **BLOCKED** before creation: `You must have at least $0.01 in your account balance to create an endpoint.` |
| Live model inference result | **NOT VERIFIED** (no endpoint ID) |
| $50 spending cap | **NOT ENFORCED**. This is the agreed experiment ceiling and stop policy, not a Runpod account setting |

The user must add Runpod credit using the [Runpod Console](https://console.runpod.io/), ideally initially $10 with automatic top-ups disabled. Never paste payment data or API credentials into GitHub. Once funded, read-only verify the account and deploy only under the bounded settings below.

## Bounded configuration (subsequently deployed as above)

The attempt used Hub `Jacob-ML/inference-worker` (community-maintained [source](https://github.com/Jacob-ML/inference-worker)) running llama.cpp, *not* the full existing wrkhorse worker. Its non-streaming input format is `openai_route: "/v1/chat/completions"` and `openai_input` with `model`, `messages`, `stream: false`.

Runpod Hub release as observed: `v1.2.3`, `cmqdzzp2e003f07johkq202sa`. It is a community image and should be reviewed before production use.

- Endpoint name: `wrkhorse-coder-pilot-20261107`
- GPU pool: `ADA_24` (one GPU); validate the final SKU and price at creation
- Workers: min **0**, max **1**; idle timeout **1 second**
- Per-job execution timeout: **180,000 ms**
- FlashBoot: off
- Container disk: **32 GB** (not a network volume)
- Model: `bartowski/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M` (Apache-2.0 quantized model, about 4.7 GB, [model card](https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF))
- `LLAMA_SERVER_CMD_ARGS`: `-hf bartowski/Qwen2.5-Coder-7B-Instruct-GGUF:Q4_K_M --ctx-size 8192 -ngl 999 --parallel 1 --alias wrkhorse-coder`
- `MAX_CONCURRENCY`: `1`

The `--alias` matches the `model` field in [../wrkhorse/runpod_pilot.py](../wrkhorse/runpod_pilot.py). The worker binds llama.cpp internally; **do not expose the raw model server to the public internet**.

**Watch for failure to start.** The reviewed community `start.sh` script waits for a "listening" log message and may fail to exit promptly if the underlying model download/startup fails. Monitor worker logs and health; delete the endpoint immediately if workers loop or stay unhealthy. An idle timeout and per-job timeout are **not hard global spend guarantees**.

## First live test procedure (carried out, see actual results above)

1. Before creating the endpoint, read aggregate Runpod billing. Confirm no existing workloads and available balance; choose GPU capacity and note the actual cost per second.
2. Deploy the candidate endpoint with **min 0, max 1**. Confirm it appears in the Runpod plugin with that configuration, no GPU worker running until requested.
3. Submit a single job from ChatGPT through its connected Runpod plugin, using `build_sample_job()` as the request body. The body contains a system instruction and the `adder/mathy.py` fixture only. Avoid sending a GitHub token, Runpod key or unrelated personal files.
4. When the job completes, parse its `output` (a list of a single OpenAI-shaped reply or equivalent) with `validate_sample_result`. The validator rejects non-final jobs, shell text, path changes outside `adder/mathy.py`, invalid hunks, and responses larger than 32 KiB. A validated patch is **diff_ready**, not a tested or merged patch.
5. Display the proposed diff and verify it changes `return left - right` to `return left + right`. A model might fail to produce a valid diff; record a failure instead of silently replacing the result with the known mock patch.
6. Inspect Runpod endpoint worker status and billing, ensure workers return to zero, and delete the endpoint if idle scaling fails. Do not reuse a stuck GPU.
7. Record the real endpoint ID, image release, job ID, status, response validity, cost, and teardown state here. Never record credentials.

Code-only tests available now (no Runpod charge):

```bash
python -m unittest discover -s tests -p 'test_runpod_pilot.py' -v
```

## Budget and permission policy

The agreed limit is **$50 total for this pilot**. It is **not a hard enforced billing cap**; these tools do not expose a verified account-level cumulative cap setting. A Runpod cost center labels spending but does not stop it. The current-day billing query may be delayed. Before each job, require an explicit bounded run, inspect actual spend and estimated next-job cost, and stop submitting when close to the ceiling. Scale-to-zero still incurs startup and worker time. Do not enable automatic top-ups or leave a Pod running unattended. Do not promise a timed shutdown without a durable independent scheduler that has been installed and tested.

No autonomous code execution, PR merge, paid upgrade, or arbitrary task dispatch is authorized by this pilot. Every GPU launch/job should be attributable to a user request in this conversation.

## Next larger milestone (not part of this pilot)

Replace this constrained ChatGPT → Runpod Serverless call with a hosted `wrkhorse` gateway and a durable queue if long-running multi-app delegation is needed. That requires real authentication, a safe repo workspace, networked state (the existing SQLite queue assumes one machine), isolated test execution, and an approval step for write operations. The existing [AI infrastructure plan](AI-INFRASTRUCTURE-PLAN.md) describes that project. Do not treat this narrow ChatGPT inference test as proof of Grok/Claude iPhone MCP connectivity.
