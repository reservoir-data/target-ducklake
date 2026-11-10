"""Singer message processing."""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from target_ducklake.sql import TableRef, columns_from_schema, table_ref

if TYPE_CHECKING:
    from collections.abc import Iterable

    from target_ducklake.config import Config
    from target_ducklake.warehouse import Warehouse

logger = logging.getLogger(__name__)

_METADATA_COLUMNS = {
    "_sdc_extracted_at": "TIMESTAMPTZ",
    "_sdc_received_at": "TIMESTAMPTZ",
    "_sdc_batched_at": "TIMESTAMPTZ",
}


class TargetError(RuntimeError):
    """The input could not be loaded."""


@dataclass(slots=True)
class _Stream:
    name: str
    table: TableRef
    columns: dict[str, str]
    keys: list[str]
    rows: list[dict[str, Any]] = field(default_factory=list)


class Target:
    """Read Singer messages and load them into DuckLake.

    Records are buffered and flushed to a single transaction once
    ``batch_size_rows`` records are waiting, and again at the end of the input.
    State is only emitted after the records that precede it were committed.
    """

    def __init__(
        self,
        config: Config,
        warehouse: Warehouse,
        output: TextIO,
    ) -> None:
        """Create a target writing state messages to ``output``."""
        self._config = config
        self._warehouse = warehouse
        self._output = output
        self._streams: dict[str, _Stream] = {}
        self._buffered = 0
        self._pending_state: dict[str, Any] | None = None
        self._emitted_state: dict[str, Any] | None = None

    def run(self, lines: Iterable[str]) -> None:
        """Process every message, then flush and run maintenance."""
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                message = json.loads(line)
                kind = message["type"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                msg = f"Line {number} is not a valid Singer message"
                raise TargetError(msg) from exc
            self._handle(kind, message)

        self.flush()
        if self._config.run_maintenance:
            self._warehouse.maintain()

    def _handle(self, kind: str, message: dict[str, Any]) -> None:
        if kind == "RECORD":
            self._record(message)
        elif kind == "SCHEMA":
            self._schema(message)
        elif kind == "STATE":
            self._pending_state = message["value"]
        elif kind == "ACTIVATE_VERSION":
            logger.warning("Ignoring ACTIVATE_VERSION for %r", message.get("stream"))
        else:
            logger.warning("Ignoring unsupported message type %r", kind)

    def _schema(self, message: dict[str, Any]) -> None:
        name = message["stream"]
        columns = columns_from_schema(message["schema"])
        if self._config.add_record_metadata:
            columns |= _METADATA_COLUMNS

        keys = list(message.get("key_properties") or [])
        if missing := [key for key in keys if key not in columns]:
            msg = f"Key properties {missing} of stream {name!r} are not in its schema"
            raise TargetError(msg)

        # Buffered rows were written for the previous schema.
        self.flush()
        table = table_ref(name, self._config.default_target_schema)
        with self._warehouse.transaction():
            self._warehouse.ensure_table(table, columns)
        self._streams[name] = _Stream(name, table, columns, keys)

    def _record(self, message: dict[str, Any]) -> None:
        name = message["stream"]
        stream = self._streams.get(name)
        if stream is None:
            msg = f"Received a record for stream {name!r} before its schema"
            raise TargetError(msg)

        row = dict(message["record"])
        if self._config.add_record_metadata:
            row["_sdc_extracted_at"] = message.get("time_extracted")
            row["_sdc_received_at"] = datetime.now(UTC).isoformat()
        stream.rows.append(row)
        self._buffered += 1
        if self._buffered >= self._config.batch_size_rows:
            self.flush()

    def flush(self) -> None:
        """Commit all buffered records, then emit the latest state."""
        if self._buffered:
            batched_at = datetime.now(UTC).isoformat()
            with self._warehouse.transaction():
                for stream in self._streams.values():
                    self._flush_stream(stream, batched_at)
            self._buffered = 0

        if (
            self._pending_state is not None
            and self._pending_state != self._emitted_state
        ):
            self._output.write(json.dumps(self._pending_state) + "\n")
            self._output.flush()
            self._emitted_state = self._pending_state

    def _flush_stream(self, stream: _Stream, batched_at: str) -> None:
        if not stream.rows:
            return

        rows = _deduplicate(stream, stream.rows)
        if self._config.add_record_metadata:
            for row in rows:
                row["_sdc_batched_at"] = batched_at

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "batch.jsonl"
            with path.open("w", encoding="utf-8") as file:
                for row in rows:
                    file.write(json.dumps(row, default=str) + "\n")
            self._warehouse.load(stream.table, stream.columns, stream.keys, path)

        logger.info("Loaded %d records into %s", len(rows), stream.table)
        stream.rows.clear()


def _deduplicate(stream: _Stream, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the last record per key; MERGE rejects several matches for one key."""
    if not stream.keys:
        return rows

    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = [row.get(k) for k in stream.keys]
        if None in key:
            msg = f"Record in stream {stream.name!r} has a null key: {row}"
            raise TargetError(msg)
        latest[json.dumps(key, default=str)] = row
    return list(latest.values())
