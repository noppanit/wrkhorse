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

The four tools are `submit_task`, `get_task_status`, `get_task_result`, and `get_project_status`. `submit_task` takes `task_name` (`sample-add` only) and `mode` (`mock` or `inference`). It returns a `job_id`. The bot then polls status and result. Jobs stay `queued` until `wrkhorse worker` is running against the same `WRKHORSE_DB`.

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

## Not done

- No OAuth metadata and no login flow.
- No check that AddMcpServer stored the header or listed the four tools.
- No check from a phone app.
- No proof the bot can poll a job that runs longer than the conversation that submitted it. The queue itself survives a client disconnect only while the worker and SQLite process keep running.
