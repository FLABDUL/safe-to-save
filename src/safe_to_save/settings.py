from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    """Explicit runtime settings for the isolated Safe to Save database."""

    database_url: str

    @classmethod
    def from_environment(cls) -> Settings:
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        return cls(database_url=database_url)
