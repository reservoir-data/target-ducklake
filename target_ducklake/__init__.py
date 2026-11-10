"""Singer target for DuckLake."""

from __future__ import annotations

from target_ducklake.config import Config, ConfigError
from target_ducklake.target import Target, TargetError

__all__ = ["Config", "ConfigError", "Target", "TargetError"]
