from __future__ import annotations

import pytest

from target_ducklake.sql import TableRef, ident, literal, sql_type, table_ref


@pytest.mark.parametrize(
    ("prop", "expected"),
    [
        ({"type": "string"}, "VARCHAR"),
        ({"type": ["string", "null"]}, "VARCHAR"),
        ({"type": "string", "format": "date-time"}, "TIMESTAMPTZ"),
        ({"type": "string", "format": "date"}, "DATE"),
        ({"type": ["null", "string"], "format": "uuid"}, "UUID"),
        ({"type": "integer"}, "BIGINT"),
        ({"type": ["integer", "null"]}, "BIGINT"),
        ({"type": "number"}, "DOUBLE"),
        ({"type": ["integer", "number"]}, "DOUBLE"),
        ({"type": "boolean"}, "BOOLEAN"),
        ({"type": "object"}, "JSON"),
        ({"type": ["array", "null"]}, "JSON"),
        ({"type": ["string", "integer"]}, "JSON"),
        ({}, "JSON"),
        ({"anyOf": [{"type": "integer"}, {"type": "null"}]}, "BIGINT"),
        ({"anyOf": [{"type": "integer"}, {"type": "string"}]}, "JSON"),
    ],
)
def test_sql_type(prop: dict, expected: str) -> None:
    assert sql_type(prop) == expected


@pytest.mark.parametrize(
    ("stream", "default", "expected"),
    [
        ("users", None, TableRef("main", "users")),
        ("tap_github-repositories", None, TableRef("tap_github", "repositories")),
        ("users", "raw", TableRef("raw", "users")),
        ("a-b", "raw", TableRef("raw", "a_b")),
        ("Weird Name!", None, TableRef("main", "weird_name")),
    ],
)
def test_table_ref(stream: str, default: str | None, expected: TableRef) -> None:
    assert table_ref(stream, default) == expected


def test_quoting() -> None:
    assert literal("it's") == "'it''s'"
    assert ident('a"b') == '"a""b"'
