from __future__ import annotations

import pytest

from target_ducklake import Config, ConfigError, TargetError

from .conftest import Lake, record, schema, state

USERS = schema(
    "users",
    ["id"],
    id={"type": "integer"},
    name={"type": ["string", "null"]},
    tags={"type": ["array", "null"]},
    joined={"type": ["string", "null"], "format": "date-time"},
)


def test_insert_and_upsert(lake: Lake) -> None:
    states = lake.run(
        [
            USERS,
            record("users", id=1, name="a", tags=["x"], joined="2026-01-01T00:00:00Z"),
            record("users", id=2, name="b", tags=[], joined=None),
            state(bookmark=1),
        ]
    )
    assert states == [{"bookmark": 1}]

    lake.run(
        [
            USERS,
            record("users", id=1, name="a2", tags=["x", "y"], joined=None),
            record("users", id=3, name="c", tags=None, joined=None),
            # Same key twice in one batch: the last one wins.
            record("users", id=3, name="c2", tags=None, joined=None),
        ]
    )

    rows = lake.query("SELECT id, name, tags::VARCHAR FROM lake.raw.users ORDER BY id")
    assert rows == [(1, "a2", '["x","y"]'), (2, "b", "[]"), (3, "c2", None)]

    [(count, extracted, batched)] = lake.query(
        "SELECT count(*), (max(_sdc_extracted_at) AT TIME ZONE 'UTC')::VARCHAR, "
        "bool_and(_sdc_batched_at IS NOT NULL) FROM lake.raw.users"
    )
    assert count == 3
    assert extracted.startswith("2026-11-10")
    assert batched is True


def test_append_without_keys(lake: Lake) -> None:
    events = schema("events", [], n={"type": "integer"})
    lake.run([events, record("events", n=1), record("events", n=1)])
    lake.run([events, record("events", n=1)])
    assert lake.query("SELECT count(*) FROM lake.raw.events") == [(3,)]


def test_all_columns_are_keys(lake: Lake) -> None:
    pairs = schema("pairs", ["a", "b"], a={"type": "integer"}, b={"type": "integer"})
    messages = [pairs, record("pairs", a=1, b=2)]
    lake.run(messages)
    lake.run(messages)
    assert lake.query("SELECT count(*) FROM lake.raw.pairs") == [(1,)]


def test_schema_evolution_adds_columns(lake: Lake) -> None:
    lake.run([schema("t", ["id"], id={"type": "integer"}), record("t", id=1)])
    lake.run(
        [
            schema("t", ["id"], id={"type": "integer"}, extra={"type": "string"}),
            record("t", id=1, extra="hello"),
            record("t", id=2, extra=None),
        ]
    )
    assert lake.query("SELECT id, extra FROM lake.raw.t ORDER BY id") == [
        (1, "hello"),
        (2, None),
    ]


def test_state_is_emitted_after_each_flush(lake: Lake) -> None:
    config = lake.make_config(batch_size_rows=2)
    states = lake.run(
        [
            USERS,
            record("users", id=1, name="a", tags=None, joined=None),
            state(n=1),
            record("users", id=2, name="b", tags=None, joined=None),
            state(n=2),
            record("users", id=3, name="c", tags=None, joined=None),
            state(n=3),
        ],
        config,
    )
    # The first flush (records 1-2) happens before state n=2 is read, so only n=1
    # is known to be fully committed. n=3 follows the final flush.
    assert states == [{"n": 1}, {"n": 3}]
    assert lake.query("SELECT count(*) FROM lake.raw.users") == [(3,)]


def test_failed_flush_rolls_back_and_emits_no_state(lake: Lake) -> None:
    messages = [
        USERS,
        record("users", id=1, name="a", tags=None, joined=None),
        record("users", id=2, name="b", tags=None, joined="not a timestamp"),
        state(n=1),
    ]
    with pytest.raises(Exception, match=r"(?i)timestamp|conversion"):
        lake.run(messages)
    assert lake.query("SELECT count(*) FROM lake.raw.users") == [(0,)]


def test_record_before_schema(lake: Lake) -> None:
    with pytest.raises(TargetError, match="before its schema"):
        lake.run([record("users", id=1)])


def test_null_key(lake: Lake) -> None:
    with pytest.raises(TargetError, match="null key"):
        lake.run([USERS, record("users", id=None, name="a", tags=None, joined=None)])


def test_key_not_in_schema(lake: Lake) -> None:
    with pytest.raises(TargetError, match="not in its schema"):
        lake.run([schema("t", ["nope"], id={"type": "integer"})])


def test_invalid_message(lake: Lake) -> None:
    from target_ducklake import Target

    with pytest.raises(TargetError, match="Line 1"):
        Target(lake.config, lake.warehouse, None).run(["{not json"])  # type: ignore[arg-type]


def test_stream_name_schema_split(lake: Lake) -> None:
    config = lake.make_config(default_target_schema=None)
    lake.run(
        [
            schema("tap_x-things", [], n={"type": "integer"}),
            record("tap_x-things", n=1),
        ],
        config,
    )
    assert lake.query("SELECT n FROM lake.tap_x.things") == [(1,)]


def test_no_metadata(lake: Lake) -> None:
    config = lake.make_config(add_record_metadata=False)
    lake.run([schema("t", [], n={"type": "integer"}), record("t", n=1)], config)
    assert [
        c
        for (c,) in lake.query(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 't'"
        )
    ] == ["n"]


def test_maintenance(lake: Lake) -> None:
    config = lake.make_config(run_maintenance=True, batch_size_rows=1)
    lake.run(
        [schema("t", [], n={"type": "integer"}), record("t", n=1), record("t", n=2)],
        config,
    )
    assert lake.query("SELECT count(*) FROM lake.raw.t") == [(2,)]


def test_config_validation() -> None:
    with pytest.raises(ConfigError, match="catalog"):
        Config.from_dict({})
    with pytest.raises(ConfigError, match="batch_size_rows"):
        Config.from_dict({"catalog": "x", "batch_size_rows": 0})
    with pytest.raises(ConfigError, match="go together"):
        Config.from_dict({"catalog": "x", "s3_access_key_id": "a"})
    with pytest.raises(ConfigError, match="s3_url_style"):
        Config.from_dict({"catalog": "x", "s3_url_style": "nope"})
