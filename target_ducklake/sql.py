"""SQL quoting helpers and JSON Schema to DuckDB type mapping."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def literal(value: str) -> str:
    """Quote a string literal."""
    return "'" + value.replace("'", "''") + "'"


def ident(name: str) -> str:
    """Quote an identifier."""
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True, slots=True)
class TableRef:
    """A schema-qualified table name."""

    schema: str
    table: str


def normalize(name: str) -> str:
    """Make a stream name safe to use as a schema or table name."""
    return re.sub(r"\W+", "_", name).strip("_").lower() or "_"


def table_ref(stream: str, default_schema: str | None) -> TableRef:
    """Map a Singer stream name to a table."""
    if default_schema:
        return TableRef(normalize(default_schema), normalize(stream))
    if "-" in stream:
        schema, _, table = stream.partition("-")
        return TableRef(normalize(schema), normalize(table))
    return TableRef("main", normalize(stream))


_STRING_FORMATS = {
    "date-time": "TIMESTAMPTZ",
    "date": "DATE",
    "time": "TIME",
    "uuid": "UUID",
}
_SCALARS = {
    "integer": "BIGINT",
    "number": "DOUBLE",
    "boolean": "BOOLEAN",
}


def sql_type(prop: Mapping[str, Any]) -> str:
    """Map a JSON Schema property to a DuckDB type.

    Anything that has no single scalar representation (objects, arrays, unions
    and untyped properties) is stored as JSON so no data is lost.
    """
    for key in ("anyOf", "oneOf"):
        if key in prop:
            options = [
                sub for sub in prop[key] if sub.get("type") not in ("null", ["null"])
            ]
            return sql_type(options[0]) if len(options) == 1 else "JSON"

    raw = prop.get("type")
    types = {raw} if isinstance(raw, str) else set(raw or ())
    types.discard("null")

    if types == {"integer", "number"}:
        return "DOUBLE"
    if len(types) != 1:
        return "JSON"

    (kind,) = types
    if kind == "string":
        return _STRING_FORMATS.get(prop.get("format", ""), "VARCHAR")
    return _SCALARS.get(kind, "JSON")


def columns_from_schema(schema: Mapping[str, Any]) -> dict[str, str]:
    """Map the top-level properties of a stream schema to column types."""
    return {name: sql_type(prop) for name, prop in schema.get("properties", {}).items()}
