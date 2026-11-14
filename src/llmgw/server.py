"""uvicorn entry points for the gateway app.

Run with:

    llmgw [config.yaml]                # canonical entry point
    uv run llmgw                        # same, via the project venv

The config path comes from the first argument or ``LLMGW_CONFIG`` (default
``/config/gateway.yaml`` inside the container). The gateway listens on the
``listen.host`` / ``listen.port`` values from the config. The SIGHUP handler
is registered on the running uvicorn event loop (design D8), so
``podman kill -s HUP`` works.

For tooling that wants to launch uvicorn itself (e.g. ``--reload``), the ASGI
factory is also available:

    uvicorn llmgw.server:create_gateway_app --factory --host 0.0.0.0 --port 8080

(When launched this way, the bind address comes from the uvicorn CLI, not
from the config.)
"""

from __future__ import annotations

import os
import sys

import uvicorn
from starlette.applications import Starlette

from .app import create_app
from .config import ConfigError, load_config


def create_gateway_app() -> Starlette:
    config_path = os.environ.get("LLMGW_CONFIG", "/config/gateway.yaml")
    return create_app(config_path)


def main(argv: list[str] | None = None) -> int:
    """Load the config and serve on its ``listen.host`` / ``listen.port``."""
    args = list(sys.argv[1:] if argv is None else argv)
    config_path = args[0] if args else os.environ.get("LLMGW_CONFIG", "/config/gateway.yaml")
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"config error: cannot read {config_path}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    uvicorn.run(create_app(config), host=config.host, port=config.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
