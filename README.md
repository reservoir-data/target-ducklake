# target-ducklake

A [Singer](https://www.singer.io) target that loads data into a [DuckLake](https://ducklake.select).

It is a small, dependency-light implementation (just `duckdb`) that does not use the Meltano SDK.

## Features

- Any DuckLake catalog backend (Postgres, SQLite, DuckDB file) and any data path DuckDB can write to, including S3-compatible stores such as Cloudflare R2 or Neon Storage.
- Upserts with `MERGE INTO` when a stream has `key_properties`; plain appends otherwise. Keys are logical only, since DuckLake has no `PRIMARY KEY` constraints.
- Atomic flushes: all streams in a batch are committed in one DuckLake transaction, and `STATE` is emitted only after the records preceding it are committed.
- Schema evolution: new properties become new columns.
- Optional maintenance after the run: merge adjacent files, expire snapshots, clean up old files.

Nested objects and arrays, and properties with mixed types, are stored as `JSON`.

## Usage

```shell
uv tool install target-ducklake
tap-something | target-ducklake --config config.json
```

```json
{
  "catalog": "postgres:postgresql://user:password@host/db",
  "data_path": "s3://bucket/warehouse/",
  "s3_endpoint": "https://s3.example.com",
  "s3_region": "us-east-1",
  "s3_url_style": "path",
  "s3_access_key_id": "...",
  "s3_secret_access_key": "...",
  "default_target_schema": "raw"
}
```

Run `target-ducklake --about` for the full list of settings.

### Semantics worth knowing

- Delivery is at-least-once. Streams with keys are idempotent; streams without keys will duplicate rows if a run is replayed.
- `ACTIVATE_VERSION` and `BATCH` messages are not supported (the former is ignored with a warning).
- Changing the type of an existing column is not handled.

## Development

```shell
uv run pytest
```

The tests use a local DuckLake (a DuckDB file plus a temporary data directory), so they need no network access beyond installing the DuckDB `ducklake` extension.
