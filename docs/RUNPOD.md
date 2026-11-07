# Runpod manual

**NOT VERIFIED.** This file is a checklist for the owner. The pilot did not create an account, pod, volume, secret, or GPU, and it did not call the Runpod API. Commands that create a pod spend money. Run them only in the Runpod console, not from this repository's tests.

Product spelling follows current Runpod docs: Runpod. GPU types and GPU prices are whatever the console shows at deploy time. This page does not list them. wrkhorse does not enforce a spend cap.

Read these pages before spending:

- [Manage Pods](https://docs.runpod.io/pods/manage-pods) (checked 2026-11-07)
- [Pod pricing](https://docs.runpod.io/pods/pricing) (checked 2026-11-07)
- [Expose ports](https://docs.runpod.io/pods/configuration/expose-ports) (checked 2026-11-07)
- [Environment variables](https://docs.runpod.io/pods/templates/environment-variables) and [secrets](https://docs.runpod.io/pods/templates/secrets) (checked 2026-11-07)
- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/) (checked 2026-11-07)

## What you are assembling

One pod runs all of the following. They are not split across machines in this slice.

1. vLLM, bound so only the pod can reach it, with its own API key.
2. `wrkhorse worker`, which calls that local URL.
3. `wrkhorse serve`, which is the only process published through the Runpod HTTPS proxy.
4. One SQLite file on the pod volume, used by both wrkhorse processes.

## 1. Choose capacity without guessing a price

In the console: Pods → Deploy. The GPU list and the on-demand price are on that screen. Pick a GPU whose memory can hold the model you choose. vLLM's older engine-argument docs describe `--gpu-memory-utilization` as a fraction from 0 to 1 (default 0.9). Confirm the flag with `vllm serve --help` on the image you actually start. This repository did not run vLLM and does not select a model.

Model choice and license are yours. A gated download may need a Hugging Face token; store that in a Runpod secret, not in git. The vLLM docs use `NousResearch/Meta-Llama-3-8B-Instruct` as their own example. That is not a wrkhorse requirement.

Template: choose a current PyTorch or vLLM template in the console. Do not treat an image tag copied from a doc example as tested by this repo. Confirm the host CUDA version against the template; the manage-pods page says a mismatch shows up as `OCI runtime create failed`.

Storage, from the pricing page on 2026-11-07 (confirm the live table; rates change):

| Storage | Running pod | Stopped pod |
| --- | --- | --- |
| Container disk | $0.10/GB/month, erased when the pod stops | Not charged |
| Volume disk | $0.10/GB/month | $0.20/GB/month, kept until the pod is deleted |
| Network volume | $0.07/GB/month under 1 TB, $0.05/GB/month over 1 TB | Same rate, kept until you delete the volume |

There is no ingress or egress fee in that page. On-demand deploy requires at least one hour of credits for the configuration. The documented default account spend limit is $80/hour across resources, and support can change it. That is an hourly account limit, not a $30–$50 total experiment budget, and wrkhorse does not read or enforce it. Pods stop at a $0 balance. A pod with a network volume is stopped and the volume is kept (storage charges continue). A pod without a network volume is terminated and its data is not recoverable.

Put the git checkout and `WRKHORSE_DB` on `/workspace` if you need them after Stop. Container disk is cleared on Stop. A network volume keeps `/workspace` even if the pod is later terminated; the volume itself keeps billing until you delete it. Network volumes must be attached during deploy.

## 2. Secrets in the console, not in git

Create secrets under Credentials → Secrets. The API rejects secret names that start with `RUNPOD`. Reference them from a template or Edit Pod as `{{ RUNPOD_SECRET_<name> }}`. Editing environment variables on a running pod restarts it and clears data that is not on the volume.

Set these on the pod. Generate the gateway token locally with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and paste it into the secret. Do not commit it.

Gateway process:

```text
WRKHORSE_AUTH_TOKEN={{ RUNPOD_SECRET_wrkhorse_gateway }}
WRKHORSE_DB=/workspace/wrkhorse/var/wrkhorse.sqlite
WRKHORSE_HOST=0.0.0.0
WRKHORSE_PORT=8765
WRKHORSE_ALLOW_PUBLIC_BIND=1
WRKHORSE_PUBLIC_HOST=<POD_ID>-8765.proxy.runpod.net
```

Worker process (omit the gateway token here if you start it from a shell that does not export it):

```text
WRKHORSE_DB=/workspace/wrkhorse/var/wrkhorse.sqlite
WRKHORSE_EXECUTION_MODE=disabled
WRKHORSE_INFERENCE_BASE_URL=http://127.0.0.1:8000/v1
WRKHORSE_INFERENCE_API_KEY={{ RUNPOD_SECRET_vllm }}
WRKHORSE_INFERENCE_MODEL=<the model id you loaded>
```

Leave `WRKHORSE_EXECUTION_MODE` at `disabled` until the gateway and the model server are both up. The switch does not create a sandbox.

## 3. Publish only the gateway port

In Edit Template or Edit Pod, put `8765` in Expose HTTP Ports. Do not put the vLLM port there.

The proxy URL is:

```text
https://<POD_ID>-8765.proxy.runpod.net/mcp
```

The docs describe that path as `https://[POD_ID]-[INTERNAL_PORT].proxy.runpod.net`. It is public HTTPS, the connection goes User → Cloudflare → Runpod load balancer → pod, and Cloudflare closes it after 100 seconds. `submit_task` only writes a row and returns a job id, so the tool call itself stays short. The worker does the slow part.

The service inside the pod must listen on `0.0.0.0`, not only `127.0.0.1`, or the proxy cannot reach it. wrkhorse still requires the bearer and the public hostname.

A pod shown as Running may still be starting. The manage page says to check the Telemetry tab.

## 4. Install on the pod

Use the web terminal or SSH from the Connect menu. Then:

```bash
cd /workspace
git clone https://github.com/noppanit/wrkhorse.git
cd wrkhorse
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
make test
```

`make test` does not need a GPU. If it fails, stop and do not point a bot at the pod.

## 5. Start the model, then the worker, then the gateway

vLLM's current server page shows:

```bash
vllm serve <MODEL> --dtype auto --api-key "$WRKHORSE_INFERENCE_API_KEY"
```

and a client using `http://localhost:8000/v1`. On the pod, run `vllm serve --help`. If `--host` is present, set `--host 127.0.0.1`. Do not rely on `--api-key` alone: the vLLM docs say it covers `/v1`, `/v2`, and `/inference`, and does **not** cover every route, including `/invocations`. Keeping the process off the public proxy is the control this runbook uses. This repository did not execute `vllm serve`.

In a second terminal:

```bash
cd /workspace/wrkhorse
. .venv/bin/activate
export WRKHORSE_DB=/workspace/wrkhorse/var/wrkhorse.sqlite
export WRKHORSE_INFERENCE_BASE_URL=http://127.0.0.1:8000/v1
export WRKHORSE_INFERENCE_API_KEY=...   # same value as vLLM, from the secret
export WRKHORSE_INFERENCE_MODEL=...
export WRKHORSE_EXECUTION_MODE=disabled
python -m wrkhorse worker
```

In a third terminal, export the gateway variables from section 2 and run:

```bash
python -m wrkhorse serve
```

The log line is the MCP URL. It does not include the token.

First remote call, still in `disabled` mode: `submit_task` with `sample-add` and `mock`. Expect a job id, then `get_task_result` with status `refused` and code `execution_disabled`. If you get `queued` forever, the worker is not using the same database file.

## 6. Opt in on the pod, then inspect

Stop the worker and start it again with `WRKHORSE_EXECUTION_MODE=trusted-local-demo` only if this pod has no private data and no extra credentials in the environment. This still is not a sandbox.

- `mode=mock` applies the reviewed one-line fix and runs the fixture unit test. A pass looks like the local `make demo` result: status `succeeded`, diff changes `return left - right` to `return left + right`, test output contains `OK`.
- `mode=inference` calls vLLM and stores a unified diff if the reply parses. Status `diff_ready` means the diff was written and **not executed**. Status `failed` / `inference_not_configured` means the URL, key, or model was missing. The worker does not open a pull request.

Artifacts on the pod:

```text
/workspace/wrkhorse/var/artifacts/<job-id>/diff.patch
/workspace/wrkhorse/var/artifacts/<job-id>/test.txt   # mock mode only
```

`get_task_result` also returns the diff text, so the bot does not need the pod filesystem.

## 7. Stop spending

From the pricing and manage-pods pages:

- **Stop** releases the GPU and keeps `/workspace` on the volume disk. You are still billed for that volume. Container disk is erased and is not billed while stopped. Console: expand the pod, Stop. CLI documented as `runpodctl pod stop POD_ID`. Resume is Start; the docs warn you can come back with zero GPUs if the host is full.
- **Terminate** deletes the pod. Data that is not on a network volume is deleted. A network volume is detached, not deleted, and its storage rate continues. Console: Stop, then the trash control. CLI documented as `runpodctl pod delete POD_ID`.
- After either action, open the Billing page and confirm the GPU line stopped. Then delete a network volume if you do not want its hourly storage charge.
- wrkhorse has no idle shutdown and no budget stop. Stopping the pod is a manual step.

## Still not verified

Creating the pod, the proxy URL, vLLM, a real model download, the Grok Bot header, and any inference quality. Local tests never left `127.0.0.1` and never used a GPU.
