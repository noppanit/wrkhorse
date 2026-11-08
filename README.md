<div align="center">

<img src="assets/wrkhorse-mark.svg" width="108" alt="Wrkhorse minimalist horse logo" />

# Wrkhorse

**A local-first MCP gateway for getting useful work done with your own models.**

[![Tests](https://github.com/noppanit/wrkhorse/actions/workflows/test.yml/badge.svg)](https://github.com/noppanit/wrkhorse/actions/workflows/test.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-222)
![MCP](https://img.shields.io/badge/protocol-MCP-5B5BD6)
![Stage](https://img.shields.io/badge/status-experimental-orange)
![Routing](https://img.shields.io/badge/routing-auto%20%7C%20local%20%7C%20cloud-334155)

**Your laptop. Your model. Your control.**

</div>

---

## Get started on your Mac

Requires **Python 3.11+**, [Ollama](https://ollama.com/download/mac), and [pipx](https://pipx.pypa.io/stable/installation/) (or `uv tool`). Installation is from GitHub; **this project is not claimed to be published on PyPI**.

```bash
pipx install git+https://github.com/noppanit/wrkhorse.git
wrkhorse init
ollama pull qwen2.5-coder:1.5b
wrkhorse start --check
wrkhorse start
```

`wrkhorse init` creates a private token and configuration in `~/.local/share/wrkhorse/`. It never downloads a model or exposes a port. The explicit `ollama pull` downloads a small coding model; `start --check` checks it without loading the model. `start` runs the authenticated MCP gateway and worker on `127.0.0.1:8765`; press Ctrl-C to stop. No cloud GPU is provisioned or charged.

To install this **draft branch** before merge, replace the URL with `git+https://github.com/noppanit/wrkhorse.git@feat/installable-library-branding`. Use a Python 3.11–3.13 interpreter for the pinned dependencies.

## How it fits together

```text
Grok Bot / other compatible MCP client
              |
      authenticated MCP
              |
         Wrkhorse
       /          \
 local Ollama   optional GPU tunnel
 (default)       (explicit opt-in)
```

- **MCP tools:** status and bounded sample task queue, plus opt-in text-only model chat.
- **Model router:** `auto` (default), `local`, `cloud`. In auto mode chat remains local; coding uses a cloud tunnel only when explicitly enabled. [Routing guide](docs/MODEL-ROUTER.md).
- **GitHub draft writing:** optional, separately authenticated and limited to `posts/drafts/*.md` on draft PR branches. [Permission guide](docs/GITHUB-DRAFTS.md).
- **Safety:** local-only install defaults, bounded requests, no arbitrary shell access, no automatic GPU provisioning or code execution from model output.

> **Current scope:** This is an experimental MCP gateway, not a general autonomous coding agent. The existing coding worker only supports the `sample-add` fixture. The owner has reported a working Grok Bot MCP connection; compatibility of this installer on the owner's Mac still needs to be verified. ChatGPT consumer mobile access to custom MCP is not established.

## Documentation

[Mac setup](docs/DEPLOY-MACBOOK.md) · [Model routing](docs/MODEL-ROUTER.md) · [Grok Bot](docs/GROK-BOT.md) · [GitHub drafts](docs/GITHUB-DRAFTS.md) · [Security and readiness](docs/READINESS.md) · [Agent handoff](AGENTS.md)

## Development

```bash
git clone https://github.com/noppanit/wrkhorse.git
cd wrkhorse
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

The GitHub Actions badge reports the real workflow status; it is not a fabricated passing-test claim. Do not commit secrets or expose Ollama directly. For authenticated remote MCP access, configure Cloudflare Access separately using [the deployment guide](docs/DEPLOY-MACBOOK.md).

<sub>Wrkhorse is under active development. Contributions and changes should go through reviewed pull requests.</sub>
