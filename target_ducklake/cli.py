"""Command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

from target_ducklake.config import SETTINGS, Config, ConfigError
from target_ducklake.target import Target, TargetError
from target_ducklake.warehouse import Warehouse

NAME = "target-ducklake"


def _version() -> str:
    try:
        return metadata.version(NAME)
    except metadata.PackageNotFoundError:
        return "0.0.0"


def _about() -> dict[str, Any]:
    return {
        "name": NAME,
        "version": _version(),
        "capabilities": ["about", "stream-maps-none"],
        "settings": [
            {
                "name": s.name,
                "kind": s.kind,
                "description": s.description,
                "value": s.default,
                "required": s.required,
                "sensitive": s.secret,
            }
            for s in SETTINGS
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=NAME, description="Load Singer data into DuckLake."
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        type=Path,
        help="JSON settings file. May be repeated; later files win.",
    )
    parser.add_argument("--about", action="store_true", help="Describe the target.")
    parser.add_argument(
        "--format",
        choices=("json",),
        default="json",
        help="Output format of --about.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the target against Singer messages on stdin."""
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.about:
        sys.stdout.write(json.dumps(_about(), indent=2) + "\n")
        return 0

    raw: dict[str, Any] = {}
    for path in args.config:
        raw |= json.loads(path.read_text(encoding="utf-8"))

    try:
        config = Config.from_dict(raw)
    except ConfigError as exc:
        logging.getLogger(NAME).error("%s", exc)  # noqa: TRY400
        return 2

    warehouse = Warehouse(config)
    try:
        Target(config, warehouse, sys.stdout).run(sys.stdin)
    except TargetError as exc:
        logging.getLogger(NAME).error("%s", exc)  # noqa: TRY400
        return 1
    finally:
        warehouse.close()
    return 0
