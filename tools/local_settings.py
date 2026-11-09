"""Read private release/export destinations from an explicit external JSON file."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class LocalSettings:
    release_directory: Path
    export_directory: Path


def load_settings(path: Path) -> LocalSettings:
    path = path.expanduser().resolve()
    if path.is_relative_to(REPO_ROOT):
        raise ValueError("Keep private settings outside the repository")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Local settings must be an object with schema_version 1")

    def directory(key: str) -> Path:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Local settings require a nonempty {key}")
        result = Path(value).expanduser()
        if not result.is_absolute():
            result = path.parent / result
        result = result.resolve()
        if result == REPO_ROOT or result.is_relative_to(REPO_ROOT):
            raise ValueError(f"{key} must be outside the repository")
        return result

    settings = LocalSettings(directory("release_directory"), directory("export_directory"))
    if settings.release_directory == settings.export_directory:
        raise ValueError("Release and export directories must differ")
    return settings
