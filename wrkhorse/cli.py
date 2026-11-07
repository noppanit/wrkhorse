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

    sub.add_parser("demo", help="local trusted demo of the reviewed sample-add fixture")

    args = parser.parse_args(argv)
    try:
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
        if args.command == "demo":
            from wrkhorse.demo import run_demo

            print(run_demo(), end="")
            return 0
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
