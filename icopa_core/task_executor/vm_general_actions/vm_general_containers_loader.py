"""Loader for static container preset definitions used as runtime fallback."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PRESET_PATH = Path(__file__).resolve().parent / "config" / "general_containers.yaml"


@dataclass(frozen=True)
class PresetContainer:
    """Normalized static preset container definition."""

    preset_id: str
    name: str
    description: str
    image: str
    run_args: str
    parameters: dict[str, Any]
    aliases: tuple[str, ...]

    def require_image(self) -> str:
        if not self.image:
            raise PresetContainersError(
                f"Preset '{self.name}' requires your container 'image' reference. "
                "Set it in your own catalog and select that file with ICOPA_PRESET_CONTAINERS_PATH."
            )
        return self.image


class PresetContainersError(ValueError):
    """Raised when static preset definitions are invalid."""


def is_preset_containers_fallback_enabled() -> bool:
    """Return whether stress preset fallback via static config is enabled."""
    raw = str(os.getenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def resolve_preset_container(preset_name: str) -> PresetContainer | None:
    """Resolve one static preset by id, alias, or name."""
    if not preset_name.strip():
        return None
    normalized_target = preset_name.strip().lower()
    for preset in load_preset_containers():
        candidates = {preset.preset_id.lower(), preset.name.lower(), *(item.lower() for item in preset.aliases)}
        if normalized_target in candidates:
            return preset
    return None


def load_preset_containers() -> list[PresetContainer]:
    """Load all static presets from configured YAML path."""
    path = Path(os.getenv("ICOPA_PRESET_CONTAINERS_PATH", str(_DEFAULT_PRESET_PATH))).expanduser()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or []

    entries: list[Any]
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        for key in ("presets", "containers", "items"):
            candidate = raw.get(key)
            if isinstance(candidate, list):
                entries = candidate
                break
        else:
            entries = []
    else:
        raise PresetContainersError(f"{path}: root must be a list or mapping.")

    out: list[PresetContainer] = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise PresetContainersError(f"{path}: preset entry at index {index} must be an object.")
        name = str(item.get("name") or "").strip()
        description = str(item.get("description") or "").strip()
        preset_id = str(item.get("id") or name).strip()
        image = str(item.get("image") or "").strip()
        run_args = str(item.get("run_args") or "").strip()
        parameters = item.get("parameters") or {}
        aliases_raw = item.get("aliases") or []
        if not name:
            raise PresetContainersError(f"{path}: preset entry at index {index} is missing 'name'.")
        if not isinstance(parameters, dict):
            raise PresetContainersError(f"{path}: preset '{name}' field 'parameters' must be an object.")
        if not isinstance(aliases_raw, list):
            raise PresetContainersError(f"{path}: preset '{name}' field 'aliases' must be a list.")
        aliases = tuple(str(alias).strip() for alias in aliases_raw if str(alias).strip())
        out.append(
            PresetContainer(
                preset_id=preset_id,
                name=name,
                description=description,
                image=image,
                run_args=run_args,
                parameters=dict(parameters),
                aliases=aliases,
            )
        )
    return out
