"""DuckLake connection and table operations."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import duckdb

from target_ducklake.sql import TableRef, ident, literal

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

    from target_ducklake.config import Config

logger = logging.getLogger(__name__)

_REMOTE_SCHEMES = {"s3", "s3a", "s3n", "r2", "gcs", "gs"}
_CATALOG_EXTENSIONS = {"postgres": "postgres", "sqlite": "sqlite", "mysql": "mysql"}


class Warehouse:
    """A DuckLake catalog attached to an in-memory DuckDB connection."""

    def __init__(self, config: Config) -> None:
        """Connect and attach the catalog."""
        self._config = config
        self._alias = config.catalog_alias
        self._con = duckdb.connect()
        self._attach()

    def _attach(self) -> None:
        config = self._config
        catalog = config.catalog.removeprefix("ducklake:")

        extensions = ["ducklake"]
        backend = catalog.partition(":")[0]
        if backend in _CATALOG_EXTENSIONS:
            extensions.append(_CATALOG_EXTENSIONS[backend])
        if (
            config.s3_endpoint
            or config.s3_access_key_id
            or urlparse(config.data_path or "").scheme in _REMOTE_SCHEMES
        ):
            extensions.append("httpfs")
        for extension in extensions:
            self._con.execute(f"INSTALL {extension}")
            self._con.execute(f"LOAD {extension}")

        if config.s3_access_key_id or config.s3_endpoint:
            self._create_s3_secret()

        options = []
        if config.data_path:
            options.append(f"DATA_PATH {literal(config.data_path)}")
            if config.override_data_path:
                options.append("OVERRIDE_DATA_PATH true")
        suffix = f" ({', '.join(options)})" if options else ""
        self._con.execute(
            f"ATTACH {literal('ducklake:' + catalog)} AS {ident(self._alias)}{suffix}"
        )
        logger.info("Attached DuckLake catalog as %r", self._alias)

    def _create_s3_secret(self) -> None:
        config = self._config
        options = {"TYPE": "s3", "URL_STYLE": literal(config.s3_url_style)}
        if config.s3_endpoint:
            parsed = urlparse(config.s3_endpoint)
            endpoint = parsed.netloc or parsed.path
            options["ENDPOINT"] = literal(endpoint)
            options["USE_SSL"] = str(parsed.scheme != "http").lower()
        if config.s3_region:
            options["REGION"] = literal(config.s3_region)
        if config.s3_access_key_id and config.s3_secret_access_key:
            options["KEY_ID"] = literal(config.s3_access_key_id)
            options["SECRET"] = literal(config.s3_secret_access_key)
        body = ", ".join(
            f"{k} {v}" if k != "TYPE" else f"TYPE {v}" for k, v in options.items()
        )
        self._con.execute(f"CREATE OR REPLACE SECRET target_ducklake_s3 ({body})")

    def _qualified(self, table: TableRef) -> str:
        return f"{ident(self._alias)}.{ident(table.schema)}.{ident(table.table)}"

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Run the enclosed statements atomically."""
        self._con.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._con.execute("ROLLBACK")
            raise
        self._con.execute("COMMIT")

    def ensure_table(self, table: TableRef, columns: Mapping[str, str]) -> None:
        """Create the table, or add any columns it is missing."""
        self._con.execute(
            f"CREATE SCHEMA IF NOT EXISTS {ident(self._alias)}.{ident(table.schema)}"
        )
        existing = {
            name.lower()
            for (name,) in self._con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_catalog = ? AND table_schema = ? AND table_name = ?",
                [self._alias, table.schema, table.table],
            ).fetchall()
        }
        qualified = self._qualified(table)

        if not existing:
            definition = ", ".join(f"{ident(n)} {t}" for n, t in columns.items())
            logger.info("Creating table %s", qualified)
            self._con.execute(f"CREATE TABLE {qualified} ({definition})")
            return

        for name, sql_type in columns.items():
            if name.lower() not in existing:
                logger.info("Adding column %s to %s", name, qualified)
                self._con.execute(
                    f"ALTER TABLE {qualified} ADD COLUMN {ident(name)} {sql_type}"
                )

    def load(
        self,
        table: TableRef,
        columns: Mapping[str, str],
        keys: Sequence[str],
        path: Path,
    ) -> None:
        """Insert (or upsert, when there are keys) a newline-delimited JSON file."""
        types = ", ".join(f"{literal(n)}: {literal(t)}" for n, t in columns.items())
        source = (
            f"(SELECT * FROM read_json(?, format = 'newline_delimited', "
            f"columns = {{{types}}}))"
        )
        qualified = self._qualified(table)
        names = [ident(name) for name in columns]

        if not keys:
            self._con.execute(
                f"INSERT INTO {qualified} ({', '.join(names)}) "
                f"SELECT {', '.join(names)} FROM {source}",
                [str(path)],
            )
            return

        on = " AND ".join(f"t.{ident(k)} = s.{ident(k)}" for k in keys)
        updates = ", ".join(
            f"{ident(c)} = s.{ident(c)}" for c in columns if c not in keys
        )
        values = ", ".join(f"s.{n}" for n in names)
        statement = f"MERGE INTO {qualified} AS t USING {source} AS s ON ({on}) "
        if updates:
            statement += f"WHEN MATCHED THEN UPDATE SET {updates} "
        statement += (
            f"WHEN NOT MATCHED THEN INSERT ({', '.join(names)}) VALUES ({values})"
        )
        self._con.execute(statement, [str(path)])

    def maintain(self) -> None:
        """Compact small files and drop snapshots and files no longer needed."""
        alias = literal(self._alias)
        older_than = f"now() - INTERVAL {literal(self._config.snapshot_retention)}"
        logger.info("Running DuckLake maintenance")
        self._con.execute(f"CALL ducklake_merge_adjacent_files({alias})")
        self._con.execute(
            f"CALL ducklake_expire_snapshots({alias}, older_than => {older_than})"
        )
        self._con.execute(
            f"CALL ducklake_cleanup_old_files({alias}, older_than => {older_than})"
        )

    def close(self) -> None:
        """Close the connection."""
        self._con.close()
