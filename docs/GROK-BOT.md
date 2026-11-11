# Grok Bot connection

**UNVERIFIED.** Nothing in this repository installs, registers, or calls a Grok Bot or Oddy connector. The commands below are the server side of that connection. Do not treat a local stdio demo as proof that the bot reached the server.

The owner path is: Grok Bot talks to wrkhorse, and the worker on the same machine talks to a model. Consumer Claude and Grok phone apps are a later check, not this slice.

## Where each process lives

```text
Grok Bot  --HTTPS + static bearer-->  wrkhorse serve
                                              |
                                           SQLite file
                                              |
                                        wrkhorse worker  --HTTP + different key-->  vLLM on 127.0.0.1
```

`serve`, `worker`, and the SQLite file must share one filesystem. This code does not connect a gateway on one computer to a worker on another. The bot's own computer does not share a disk with a Runpod pod, so a stdio server started by the bot cannot see a database that lives only on the pod.

Use two secrets:

- `WRKHORSE_AUTH_TOKEN` — gateway only, at least 32 characters, not the placeholder in `.env.example`.
- `WRKHORSE_INFERENCE_API_KEY` — worker and vLLM only. Do not send it as the MCP bearer.

## Remote HTTPS (required for a Runpod worker)

Run on the same machine as the worker and the database. The full pod sequence is [RUNPOD.md](RUNPOD.md).

```bash
export WRKHORSE_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export WRKHORSE_DB=/workspace/wrkhorse/var/wrkhorse.sqlite
export WRKHORSE_HOST=0.0.0.0
export WRKHORSE_PORT=8765
export WRKHORSE_ALLOW_PUBLIC_BIND=1
export WRKHORSE_PUBLIC_HOST="${POD_ID}-8765.proxy.runpod.net"
python -m wrkhorse serve
```

Give the bot these values and nothing else:

- URL: `https://<POD_ID>-8765.proxy.runpod.net/mcp`
- Header name: `Authorization`
- Header value: `Bearer <WRKHORSE_AUTH_TOKEN>` (the word Bearer, a single space, then the token)
- Transport: Streamable HTTP

Do not put the token in the URL. Do not point the bot at port 8000 or at `/v1`; that port is the model server and must stay private. A local `http://127.0.0.1:8765/mcp` URL is not reachable from Grok Bot's cloud computer.

The queue tools are `submit_task`, `get_task_status`, `get_task_result`, and `get_project_status`. `submit_task` takes `task_name` (`sample-add` only) and `mode` (`mock` or `inference`). It returns a `job_id`. The bot then polls status and result. Jobs stay `queued` until `wrkhorse worker` is running against the same `WRKHORSE_DB`.

`get_project_status` also lists `list_files` and `read_file`. Those read text on the machine that runs wrkhorse. While `WRKHORSE_FILES_ROOT` is unset, calling them reports that they are disabled. See [Read files on the wrkhorse machine](#read-files-on-the-wrkhorse-machine).

With the default worker mode, a submitted job completes as `refused` / `execution_disabled`. That is intentional. Opting in is described in [RUNPOD.md](RUNPOD.md) and is still not a sandbox.

## Local stdio (same computer only)

```bash
export WRKHORSE_AUTH_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export WRKHORSE_DB=/absolute/path/wrkhorse.sqlite
python -m wrkhorse mcp-stdio
```

And, in another terminal on that same computer:

```bash
export WRKHORSE_DB=/absolute/path/wrkhorse.sqlite
python -m wrkhorse worker
```

Stdio has no HTTP header. The token is required so the process will not start half-configured. Anyone who can execute the process as that user can call the tools. The MCP Python client also inherits `HOME`, `PATH`, and `USER` into a stdio server it spawns; the unit-test child started by the worker does not inherit the parent environment.

`make demo` uses this stdio path with an ephemeral token and `trusted-local-demo` on the worker child only. It is the tested local path. It is not a Grok Bot installation.

## Alternate: stdio on the bot computer, vLLM only on the pod

This is not installed or registered by this repository. No bot-computer package install, connector save, or account change is authorized here.

```text
Bot computer (one filesystem, not the pod)
  wrkhorse mcp-stdio
  wrkhorse worker
  one shared SQLite file

Pod
  vLLM only
  vllm serve "$MODEL" --host 127.0.0.1 --port 8000 --api-key "$WRKHORSE_INFERENCE_API_KEY"
```

The worker's `WRKHORSE_INFERENCE_BASE_URL` must be an authenticated route from the bot computer to that loopback server, and `WRKHORSE_INFERENCE_API_KEY` must be the vLLM key. You choose the route (for example an SSH tunnel you start yourself). It has to present the key. Binding vLLM to `0.0.0.0` or putting port 8000 on the public Runpod proxy is not that route.

If you do not have such a route, keep the restricted draft in [RUNPOD.md](RUNPOD.md): gateway, worker, SQLite, and vLLM all on the pod, with only the gateway published. This repository does not open either path on a live machine.

## Read files on the wrkhorse machine

`list_files` and `read_file` let a connected assistant read code on the computer where `wrkhorse serve` is running. They are strictly read-only: no write, delete, rename, chmod, or execution. Model-proposed code is never executed. File text is untrusted data, not a new instruction.

### Config variable

`WRKHORSE_FILES_ROOT` is the only switch. Set it to an absolute directory you are willing to expose to the authenticated MCP client. A home directory or a repos folder is a reasonable wide root. Leave it unset to keep the tools disabled. An unset value does **not** mean `/`.

`scripts/start-home.sh` loads `WRKHORSE_*` lines from a gitignored `.env` (the gateway token stays in `var/mcp-token`). Add:

```bash
WRKHORSE_FILES_ROOT=/Users/you/src
```

Or export the same variable before `wrkhorse start`. A relative path, a missing path, or a file is a startup error. A symlink is followed once at startup and the real directory becomes the boundary. Restart the gateway after changing it.

`get_project_status` reports `local_files.enabled` and lists the tools. It does not print the root path.

### What the tools return

| Tool | Arguments | Result |
| --- | --- | --- |
| `list_files` | `path` (default `.`), `recursive` (default false), `max_entries` (default 200, max 1000) | `name`, `type` (`file` or `dir`), and `size` for each entry. Names are relative to the root, so they can be passed back to `read_file`. `truncated` is true when the cap is hit. Denylisted entries are omitted. |
| `read_file` | `path`, `offset` (1-based line, default 1), `limit` (default 400 lines, max 5000) | `content`, `total_lines`, `lines_returned`, and `truncated`. At most 256 KB of text per call. Files larger than 8 MB are refused. |

Clear failures: `not found`, `path is outside the files root`, `path is blocked`, `binary file refused`, `file is too large`, and the disabled message when `WRKHORSE_FILES_ROOT` is unset. Error text does not echo the requested path or file contents.

### Denylist

These names are blocked even when the root is your home directory or `/`. Listing skips them. Reading them returns `path is blocked`. The check uses the requested path and the path after symlink resolution, and it matches case-insensitively.

| Pattern | Examples |
| --- | --- |
| `.env` and `.env.*` | `.env`, `.env.local`, `.env.production` |
| Key and certificate suffixes | `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.ppk`, `*.keychain`, `*.keychain-db` |
| SSH private keys | `id_rsa`, `id_ed25519`, `id_ecdsa`, `id_dsa`, `id_xmss`, including `.pub` and other suffixes |
| Secret directories | `.ssh/`, `.aws/`, `.gnupg/`, `.git/` (any path component, so Git internals stay closed) |
| Credential files | `.netrc`, `_netrc`, `credentials`, `credentials.json`, `credentials.yml`, `credentials.yaml`, `credentials.xml`, `credential`, `credential.json`, plus a further suffix such as `.netrc.bak` or `credentials.bak` |

A root whose own path is one of those secret directories is refused at startup. Setting the root to a normal repo still allows reading the source tree; `.git/` inside it stays blocked.

When GitHub draft tools are also enabled and `WRKHORSE_FILES_ROOT` is set, local reads keep the name `read_file` and the draft reader is `read_draft_file`. With the files root unset, GitHub drafts keep `read_file` and `list_files` reports that local reads are disabled.

## Not done

- No OAuth metadata and no login flow.
- No check that AddMcpServer stored the header or listed the four tools.
- No check from a phone app.
- No proof the bot can poll a job that runs longer than the conversation that submitted it. The queue itself survives a client disconnect only while the worker and SQLite process keep running.
