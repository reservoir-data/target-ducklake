"""Target settings."""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields
from typing import Any, Self

logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """The target configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Setting:
    """Description of a single setting, used by ``--about``."""

    name: str
    kind: str
    description: str
    default: Any = None
    required: bool = False
    secret: bool = False


SETTINGS = (
    Setting(
        "catalog",
        "string",
        "DuckLake metadata catalog, the part after `ducklake:` in an ATTACH "
        "statement. For example `postgres:postgresql://user:pass@host/db`, "
        "`sqlite:meta.sqlite` or `metadata.ducklake`.",
        required=True,
        secret=True,
    ),
    Setting(
        "data_path",
        "string",
        "Where data files are written, e.g. `s3://bucket/prefix/`. Only needed "
        "the first time a catalog is used; it is stored in the catalog afterwards.",
    ),
    Setting(
        "override_data_path",
        "boolean",
        "Use `data_path` even if the catalog was initialized with another one.",
        default=False,
    ),
    Setting(
        "catalog_alias",
        "string",
        "Name the catalog is attached as.",
        default="lake",
    ),
    Setting(
        "default_target_schema",
        "string",
        "Schema for all streams. When unset, streams named `<schema>-<table>` "
        "are split on the first dash and others go to `main`.",
    ),
    Setting(
        "batch_size_rows",
        "integer",
        "Number of buffered records that triggers a flush of all streams.",
        default=100_000,
    ),
    Setting(
        "add_record_metadata",
        "boolean",
        "Add `_sdc_extracted_at`, `_sdc_received_at` and `_sdc_batched_at` columns.",
        default=True,
    ),
    Setting(
        "s3_endpoint",
        "string",
        "Endpoint of an S3-compatible store. An `https://` or `http://` prefix is "
        "honored.",
    ),
    Setting("s3_region", "string", "Region of the S3-compatible store."),
    Setting(
        "s3_url_style",
        "string",
        "`path` or `vhost`.",
        default="vhost",
    ),
    Setting("s3_access_key_id", "string", "S3 access key ID.", secret=True),
    Setting("s3_secret_access_key", "string", "S3 secret access key.", secret=True),
    Setting(
        "run_maintenance",
        "boolean",
        "After the run, merge adjacent files, expire old snapshots and delete "
        "unreferenced files.",
        default=False,
    ),
    Setting(
        "snapshot_retention",
        "string",
        "How long to keep snapshots when `run_maintenance` is on, as a DuckDB "
        "interval.",
        default="7 days",
    ),
)


@dataclass(frozen=True, slots=True)
class Config:
    """Validated target configuration."""

    catalog: str
    data_path: str | None = None
    override_data_path: bool = False
    catalog_alias: str = "lake"
    default_target_schema: str | None = None
    batch_size_rows: int = 100_000
    add_record_metadata: bool = True
    s3_endpoint: str | None = None
    s3_region: str | None = None
    s3_url_style: str = "vhost"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    run_maintenance: bool = False
    snapshot_retention: str = "7 days"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Self:
        """Build a config from decoded JSON, rejecting invalid values."""
        known = {f.name for f in fields(cls)}
        for key in sorted(raw.keys() - known):
            logger.warning("Ignoring unknown setting %r", key)
        values = {k: v for k, v in raw.items() if k in known and v is not None}

        if not values.get("catalog"):
            msg = "Setting 'catalog' is required"
            raise ConfigError(msg)

        config = cls(**values)
        if config.batch_size_rows < 1:
            msg = "Setting 'batch_size_rows' must be at least 1"
            raise ConfigError(msg)
        if config.s3_url_style not in {"path", "vhost"}:
            msg = "Setting 's3_url_style' must be 'path' or 'vhost'"
            raise ConfigError(msg)
        if bool(config.s3_access_key_id) != bool(config.s3_secret_access_key):
            msg = "Settings 's3_access_key_id' and 's3_secret_access_key' go together"
            raise ConfigError(msg)
        return config
