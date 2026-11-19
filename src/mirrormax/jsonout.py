"""Plain, stable JSON output for ``--json``.

Every document has a ``"schema"`` key (``"mirrormax.<kind>/1"``). Fields are
only ever added within a schema version, never renamed or removed.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from mirrormax.layouts import Layout

SCHEMA_VERSION = 1


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses, enums, paths, datetimes and sets to JSON types.

    Args:
        value: Any value produced by mirrormax.

    Returns:
        A structure of dicts, lists, strings, numbers, booleans and ``None``.
    """
    if isinstance(value, Layout):
        return {
            "name": value.name,
            "args": value.args,
            "display_ids": sorted(value.display_ids),
            "mirrored": value.is_mirrored,
        }
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, frozenset | set):
        return sorted(to_jsonable(v) for v in value)
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    return value


def document(kind: str, payload: Any) -> str:
    """Render a JSON document with a schema tag.

    Args:
        kind: Document kind, e.g. ``status`` or ``outcome``.
        payload: The data (dataclass or mapping).

    Returns:
        Pretty-printed JSON text (no trailing newline).
    """
    data = to_jsonable(payload)
    body: dict[str, Any] = {"schema": f"mirrormax.{kind}/{SCHEMA_VERSION}"}
    if isinstance(data, dict):
        body.update(data)
    else:
        body["items"] = data
    return json.dumps(body, indent=2, ensure_ascii=False)
