"""Crash-safe atomic writes for legacy runtime files."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(
    destination: Path, contents: str, *, encoding: str = "utf-8"
) -> None:
    """Write text completely before atomically replacing the destination."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding=encoding, newline="") as temporary:
            temporary.write(contents)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def atomic_write_json(destination: Path, value: Any) -> None:
    """Serialize JSON with legacy-compatible defaults and replace atomically."""
    atomic_write_text(destination, json.dumps(value))
