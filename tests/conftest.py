from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING, Any

import pytest

from target_ducklake import Config, Target
from target_ducklake.warehouse import Warehouse

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class Lake:
    """A local DuckLake plus helpers to feed it Singer messages."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.config = self.make_config()
        self.warehouse = Warehouse(self.config)

    def make_config(self, **overrides: Any) -> Config:
        raw = {
            "catalog": str(self.tmp_path / "meta.ducklake"),
            "data_path": str(self.tmp_path / "data") + "/",
            "default_target_schema": "raw",
            **overrides,
        }
        return Config.from_dict(raw)

    def run(
        self, messages: list[dict[str, Any]], config: Config | None = None
    ) -> list[Any]:
        """Run the target and return the state messages it emitted."""
        output = io.StringIO()
        lines = [json.dumps(m) for m in messages]
        Target(config or self.config, self.warehouse, output).run(lines)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def query(self, sql: str) -> list[tuple[Any, ...]]:
        return self.warehouse._con.execute(sql).fetchall()


@pytest.fixture
def lake(tmp_path: Path) -> Iterator[Lake]:
    lake = Lake(tmp_path)
    yield lake
    lake.warehouse.close()


def schema(
    stream: str, keys: list[str], **properties: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "SCHEMA",
        "stream": stream,
        "schema": {"type": "object", "properties": properties},
        "key_properties": keys,
    }


def record(stream: str, **values: Any) -> dict[str, Any]:
    return {
        "type": "RECORD",
        "stream": stream,
        "record": values,
        "time_extracted": "2026-11-10T00:00:00+00:00",
    }


def state(**values: Any) -> dict[str, Any]:
    return {"type": "STATE", "value": values}
