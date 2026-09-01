"""Proveniência: versão da pipeline, carimbo de coleta e registro de execução."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from jmod import PIPELINE_VERSION

__all__ = ["PIPELINE_VERSION", "new_run_id", "now"]


def now() -> str:
    """Timestamp UTC em ISO-8601, com segundos. Único formato usado no banco."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]
