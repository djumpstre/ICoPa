"""Runtime environment image loader for profiling execution templates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

_SUPPORTED_PRESET_KEYS = ("commandPresets", "command-presets", "command_presets")
_SUPPORTED_GROUP_KEYS = ("commandGroups", "command_groups")
_SUPPORTED_PROFILING_GROUP_KEYS = ("profilingCommandGroups", "profiling_command_groups")
_LEGACY_GROUP_KEY_MAP = {
    "commandPresetsForRouting": "routing",
    "command_presets_for_routing": "routing",
    "commandPresetsTrafficGenerator": "testing",
    "command_presets_traffic_generator": "testing",
}
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class RuntimeEnvImageError(ValueError):
    """Raised when runtime environment YAML is invalid."""


@dataclass(frozen=True)
class CommandPreset:
    """Execution command preset for a runtime role."""

    name: str
    executor: str
    command: str


@dataclass(frozen=True)
class RuntimeEnvImage:
    """Parsed runtime environment definition."""

    name: str
    runner_image: str
    tags: dict[str, Any]
    parameters: dict[str, Any]
    artifacts: dict[str, Any]
    command_presets: dict[str, CommandPreset]
    command_groups: dict[str, list[str]]

    @property
    def image_name(self) -> str:
        """Backward-compatible alias for runner image."""
        return self.runner_image

    def check_image(self) -> bool:
        """Best-effort image field check (registry reachability is out of scope here)."""
        return bool(self.runner_image)

    def get_preset(self, preset_name: str) -> CommandPreset:
        """Return command preset by name."""
        try:
            return self.command_presets[preset_name]
        except KeyError as exc:
            raise RuntimeEnvImageError(f"Unknown command preset '{preset_name}'.") from exc

    def get_group_presets(self, group_name: str) -> list[CommandPreset]:
        """Return presets in the declared order for a command group."""
        preset_names = self.command_groups.get(group_name, [])
        if not preset_names:
            raise RuntimeEnvImageError(f"Unknown or empty command group '{group_name}'.")
        return [self.get_preset(name) for name in preset_names]

    def render_preset_command(
        self,
        preset_name: str,
        overrides: Mapping[str, Any] | None = None,
    ) -> str:
        """Render a preset command by replacing ${...} parameters."""
        context: dict[str, Any] = {"runner_image": self.runner_image, **self.parameters}
        if overrides:
            context.update(overrides)
        command = self.get_preset(preset_name).command
        return render_template(command, context)

    def gen_command(
        self,
        preset_name: str,
        overrides: Mapping[str, Any] | None = None,
    ) -> str:
        """Backward-compatible wrapper to render preset command."""
        return self.render_preset_command(preset_name, overrides=overrides)

    def post_setup_command(self) -> str:
        """Simple post-setup verification command."""
        return f"docker run --rm {self.runner_image} echo 'Runtime environment setup complete.'"


class RuntimeEnvImageLoader:
    """Loader for runtime environment image YAML definitions."""

    @classmethod
    def load(cls, yaml_path: str | Path) -> RuntimeEnvImage:
        """Load runtime environment YAML from disk."""
        path = Path(yaml_path)
        with path.open("r", encoding="utf-8") as file:
            data = yaml.safe_load(file)
        return cls.from_dict(data, source=str(path))

    @classmethod
    def from_dict(cls, data: Any, source: str = "<dict>") -> RuntimeEnvImage:
        """Validate and convert a parsed mapping into RuntimeEnvImage."""
        if not isinstance(data, Mapping):
            raise RuntimeEnvImageError(f"{source}: YAML root must be a mapping.")

        metadata = _as_dict(data.get("metadata"), default={})
        spec = _as_dict(data.get("spec"), field="spec", source=source)
        images = _as_dict(spec.get("images"), field="spec.images", source=source)
        runner_image = images.get("runnerImage")
        if not isinstance(runner_image, str) or not runner_image.strip():
            raise RuntimeEnvImageError(
                f"{source}: set 'spec.images.runnerImage' to a non-empty container image reference you provide."
            )

        presets_raw = None
        for key in _SUPPORTED_PRESET_KEYS:
            if key in spec:
                presets_raw = spec[key]
                break
        groups_raw = cls._read_group_config(spec)

        presets: dict[str, CommandPreset] = {}
        group_order: dict[str, list[str]] = {}
        if presets_raw is not None:
            if not isinstance(presets_raw, list):
                raise RuntimeEnvImageError(f"{source}: command presets must be a list.")
            cls._add_presets(
                presets,
                group_order,
                presets_raw,
                source=source,
                group_name="default",
            )
        if groups_raw:
            for group_name, entries in groups_raw.items():
                if not isinstance(entries, list):
                    raise RuntimeEnvImageError(
                        f"{source}: command group '{group_name}' must be a list."
                    )
                cls._add_presets(
                    presets,
                    group_order,
                    entries,
                    source=source,
                    group_name=group_name,
                )

        if not presets:
            raise RuntimeEnvImageError(f"{source}: command presets list cannot be empty.")

        command_groups = {group_name: names for group_name, names in group_order.items() if names}

        return RuntimeEnvImage(
            name=str(metadata.get("name", "runtime-env")),
            runner_image=runner_image.strip(),
            tags=_as_dict(metadata.get("tags") if metadata.get("tags") is not None else spec.get("tags"), default={}),
            parameters=_as_dict(spec.get("parameters"), default={}),
            artifacts=_as_dict(spec.get("artifacts"), default={}),
            command_presets=presets,
            command_groups=command_groups,
        )

    @staticmethod
    def _read_group_config(spec: Mapping[str, Any]) -> dict[str, Any]:
        groups: dict[str, Any] = {}
        for key in _SUPPORTED_GROUP_KEYS:
            groups_raw = spec.get(key)
            if groups_raw is not None:
                if not isinstance(groups_raw, Mapping):
                    raise RuntimeEnvImageError(f"spec.{key} must be a mapping.")
                groups.update(dict(groups_raw))
                break

        for key in _SUPPORTED_PROFILING_GROUP_KEYS:
            profiling_raw = spec.get(key)
            if profiling_raw is None:
                continue
            if isinstance(profiling_raw, list):
                groups["profiling"] = list(profiling_raw)
                continue
            if isinstance(profiling_raw, Mapping):
                entries = profiling_raw.get("profiling")
                if not isinstance(entries, list):
                    raise RuntimeEnvImageError(f"spec.{key}.profiling must be a list.")
                groups["profiling"] = list(entries)
                continue
            raise RuntimeEnvImageError(f"spec.{key} must be a list or mapping.")

        for key, group_name in _LEGACY_GROUP_KEY_MAP.items():
            if key in spec:
                groups[group_name] = spec[key]
        return groups

    @classmethod
    def _add_presets(
        cls,
        presets: dict[str, CommandPreset],
        group_order: dict[str, list[str]],
        entries: list[Any],
        *,
        source: str,
        group_name: str,
    ) -> None:
        for entry in entries:
            preset = cls._parse_preset_entry(entry, source)
            existing = presets.get(preset.name)
            if existing is None:
                presets[preset.name] = preset
            elif existing != preset:
                raise RuntimeEnvImageError(
                    f"{source}: duplicate command preset name '{preset.name}' with "
                    "conflicting content."
                )
            group_order.setdefault(group_name, [])
            if preset.name not in group_order[group_name]:
                group_order[group_name].append(preset.name)

    @staticmethod
    def _parse_preset_entry(entry: Any, source: str) -> CommandPreset:
        if not isinstance(entry, Mapping):
            raise RuntimeEnvImageError(f"{source}: each command preset must be a mapping.")

        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeEnvImageError(f"{source}: command preset is missing a valid 'name'.")

        if "run" in entry:
            run_block = _as_dict(
                entry.get("run"),
                field=f"preset '{name}'.run",
                source=source,
            )
            executor = run_block.get("executor", "auto")
            command = run_block.get("command")
        else:
            executor = entry.get("executor", "auto")
            command = entry.get("command")

        if not isinstance(executor, str) or not executor.strip():
            raise RuntimeEnvImageError(
                f"{source}: preset '{name}' must define a non-empty executor."
            )
        if not isinstance(command, str) or not command.strip():
            raise RuntimeEnvImageError(
                f"{source}: preset '{name}' must define a non-empty command."
            )

        return CommandPreset(name=name.strip(), executor=executor.strip(), command=command)


def render_template(template: str, values: Mapping[str, Any]) -> str:
    """Render ${key} placeholders in template with the provided values."""
    missing_keys: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            missing_keys.add(key)
            return match.group(0)
        value = values[key]
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        if value is None:
            return ""
        return str(value)

    rendered = _PLACEHOLDER_PATTERN.sub(_replace, template)
    if missing_keys:
        missing = ", ".join(sorted(missing_keys))
        raise RuntimeEnvImageError(f"Missing template parameters: {missing}.")
    return rendered


def _as_dict(
    value: Any,
    *,
    field: str | None = None,
    source: str | None = None,
    default: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert mapping-like values to plain dict."""
    if value is None:
        return {} if default is None else dict(default)
    if not isinstance(value, Mapping):
        location = field or "value"
        prefix = f"{source}: " if source else ""
        raise RuntimeEnvImageError(f"{prefix}{location} must be a mapping.")
    return dict(value)
