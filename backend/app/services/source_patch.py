"""Apply a reviewed ``update`` patch to a book source.

Only reachable through the approval flow (``POST /api/source-changes/{id}/review``
with ``action=approve``): the AI never calls this on its own.  The patch is
applied key-by-key against an allow-list so a proposal can never set arbitrary
model attributes, and ``config`` is merged (not replaced) so a proposal that
mentions one rule cannot wipe the rest of the book source.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from app.services.source_interval import MAX_SYNC_INTERVAL_SECONDS

#: Book source columns a patch may change.  Anything else is ignored.
PATCHABLE_COLUMNS = (
    "name",
    "url",
    "plugin_name",
    "enabled",
    "is_r18",
    "sync_interval_seconds",
)

#: Guard against a runaway patch (the whole rule set is usually far smaller).
MAX_PATCH_SERIALIZED = 400_000


def deep_merge(base: Any, patch: Any) -> Any:
    """Recursively merge dicts; anything else is replaced by the patch."""
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = dict(base)
        for key, value in patch.items():
            merged[key] = deep_merge(base.get(key), value)
        return merged
    return patch


def _coerce_column(column: str, value: Any) -> tuple[bool, Any]:
    if column in ("enabled", "is_r18"):
        if isinstance(value, bool):
            return True, value
        text = str(value).strip().lower()
        if text in ("true", "false"):
            return True, text == "true"
        return False, None
    if column == "sync_interval_seconds":
        # A site's 拉取间隔: ``None``/"" clears it back to the source default.
        if value is None or (isinstance(value, str) and not value.strip()):
            return True, None
        # Booleans are ints in Python; ``True`` is not a request interval.
        if isinstance(value, bool):
            return False, None
        try:
            return True, max(0, min(int(float(str(value).strip())), MAX_SYNC_INTERVAL_SECONDS))
        except (TypeError, ValueError):
            return False, None
    if isinstance(value, (dict, list)):
        return False, None
    return True, str(value)


def apply_source_patch(source: Any, patch: dict[str, Any] | None) -> list[str]:
    """Apply ``patch`` to a ``Source`` ORM object; returns the applied paths.

    Top-level ``config`` is deep-merged; the allow-listed columns are set with
    type coercion.  Unknown keys are ignored and reported in the log.
    """
    patch = patch if isinstance(patch, dict) else {}
    try:
        encoded = json.dumps(patch, ensure_ascii=False)
    except (TypeError, ValueError):
        logger.warning("Refusing to apply a non-serialisable source patch")
        return []
    if len(encoded) > MAX_PATCH_SERIALIZED:
        logger.warning("Refusing to apply an oversized source patch ({} bytes)", len(encoded))
        return []

    applied: list[str] = []

    config_patch = patch.get("config")
    if isinstance(config_patch, dict) and config_patch:
        current = source.config if isinstance(source.config, dict) else {}
        merged = deep_merge(current, config_patch)
        if merged != current:
            source.config = merged
            applied.extend(_paths_of(config_patch, prefix="config"))

    for column in PATCHABLE_COLUMNS:
        if column not in patch:
            continue
        ok, value = _coerce_column(column, patch[column])
        if not ok:
            logger.warning("Ignoring non-coercible value for source.{}", column)
            continue
        if getattr(source, column, None) != value:
            setattr(source, column, value)
            applied.append(column)

    unknown = sorted(set(patch) - {"config", *PATCHABLE_COLUMNS})
    if unknown:
        logger.warning("Ignoring non-patchable keys in source patch: {}", unknown)
    return applied


def _paths_of(data: dict[str, Any], prefix: str = "") -> list[str]:
    paths: list[str] = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            paths.extend(_paths_of(value, path))
        else:
            paths.append(path)
    return paths


__all__ = ["PATCHABLE_COLUMNS", "apply_source_patch", "deep_merge"]
