"""Command line. Library calls do not print secrets."""

from __future__ import annotations

import argparse
import sys

from wrkhorse.config import ConfigError, load_gateway_settings, load_worker_settings
from wrkhorse.logsetup import configure_logging, get_logger
from wrkhorse.server import public_mcp_url, serve_http, serve_stdio
from wrkhorse.store import Store
from wrkhorse.worker import Worker, worker_id


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(prog="wrkhorse")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Streamable HTTP MCP gateway on loopback by default")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    sub.add_parser("mcp-stdio", help="stdio MCP gateway; does not run jobs")

    worker = sub.add_parser("worker", help="claim jobs from the shared SQLite database")
    worker.add_argument("--once", action="store_true")

    demo = sub.add_parser("demo", help="local trusted demo of the reviewed sample-add fixture")
    demo.add_argument(
        "--inference",
        action="store_true",
        help="exercise the inference-only worker against a stub loopback model server (network-free)",
    )

    doctor = sub.add_parser(
        "doctor", help="check the local model server is reachable and has the model"
    )
    doctor.add_argument("--warm", action="store_true", help="also load the model with one tiny request")

    setup = sub.add_parser("init", help="initialize private user configuration without downloads")
    setup.add_argument("--model", default="qwen2.5-coder:1.5b")
    startup = sub.add_parser("start", help="run the installed local MCP gateway and worker")
    startup.add_argument("--check", action="store_true", help="check Ollama without starting services")

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            from wrkhorse.installer import init
            return init(model=args.model)
        if args.command == "start":
            from wrkhorse.installer import start
            return start(check=args.check)
        if args.command == "serve":
            settings = load_gateway_settings(host=args.host, port=args.port)
            get_logger(__name__).info("listening on %s", public_mcp_url(settings))
            serve_http(settings)
            return 0
        if args.command == "mcp-stdio":
            settings = load_gateway_settings()
            get_logger(__name__).info(
                "stdio gateway ready; start wrkhorse worker against the same WRKHORSE_DB"
            )
            serve_stdio(settings)
            return 0
        if args.command == "worker":
            settings = load_worker_settings()
            store = Store(
                settings.db_path,
                stale_seconds=settings.stale_seconds,
                max_attempts=settings.max_attempts,
            )
            running = Worker(settings, store, worker_id())
            if args.once:
                running.once()
                return 0
            running.serve_forever()
            return 0
        if args.command == "doctor":
            from wrkhorse.doctor import run_doctor

            return run_doctor(warm=args.warm)
        if args.command == "demo":
            from wrkhorse.demo import run_demo

            print(run_demo(mode="inference" if args.inference else "mock"), end="")
            return 0
    except (ConfigError, RuntimeError, ValueError, OSError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
