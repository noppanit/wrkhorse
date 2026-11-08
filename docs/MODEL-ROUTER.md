# Configurable inference router (draft PR)

This change preserves the existing Grok -> authenticated MCP -> Wrkhorse tools. No MCP tool is renamed, no tunnel or GPU is deployed, and no RunPod API is called.

## Modes

- `auto` (default): `ask_local_model` stays on Mac Ollama. The bounded coding worker prefers the rented GPU **only after explicit opt-in**, then falls back to local inference on a model request failure. Without opt-in, all tasks remain local.
- `local`: all inference stays on the existing local endpoint.
- `cloud`: chat and coding inference use the cloud model through an owner-established loopback tunnel. Startup fails unless cloud routing is explicitly enabled.

**Important:** The existing coding worker only supports a bounded sample diff task. This PR does NOT add autonomous multi-file coding or shell execution.

## Configuration (example only)

Keep existing `WRKHORSE_INFERENCE_BASE_URL=http://127.0.0.1:11434/v1` and `WRKHORSE_INFERENCE_MODEL` unchanged.

```sh
WRKHORSE_MODEL_ROUTING=auto
# Cloud is OFF unless all three are configured and enabled.
# WRKHORSE_CLOUD_INFERENCE_ENABLED=1
# WRKHORSE_CLOUD_INFERENCE_BASE_URL=http://127.0.0.1:11435/v1
# WRKHORSE_CLOUD_INFERENCE_MODEL=your-downloaded-coding-model
```

A tunnel, if established later by the owner, could forward Mac `127.0.0.1:11435` to the remote GPU server's `127.0.0.1:11434`. **Do not expose the remote Ollama port to the public Internet.** Only loopback endpoints are permitted for the cloud route. The tunnel does not rent, start, stop or pay for a GPU.

The cloud model name must be a locally served tag on the remote GPU, **not** an Ollama `:cloud` tag. The gateway's existing `ask_local_model` MCP tool name remains unchanged for compatibility, even in explicit cloud mode; its result includes an inference location label.

## Safe verification (no GPU charges)

```sh
python -m unittest tests.test_model_router
python -m unittest discover -s tests
```

With cloud disabled, check `get_project_status` and `ask_local_model` through the existing Grok MCP connection. Expected: `routing=auto`, `cloud_configured=false`, and chat still uses local Ollama. The tests are offline; actual Mac and Grok integration must be rechecked after merge.

## Limits and follow-ups

- Auto routing is **task-class based**, not an intelligent cost/complexity classifier. Chat stays local; the existing sample coding task uses cloud first only when explicitly enabled.
- The tunnel and remote inference server must already be running. This change cannot create charges by provisioning hardware, but using an already running rented GPU can accrue usage fees.
- Model request retries/fallbacks are bounded to one call per candidate; the existing worker's outer job retry policy still applies.
- This router is not a secure general-purpose cloud inference proxy. Public HTTPS endpoints and API credentials are intentionally not supported; future authenticated remote providers require separate security design.
- Keep the existing Cloudflare MCP authentication, GitHub draft-only permissions, and sandbox boundaries unchanged.
