from __future__ import annotations

from pathlib import Path

import pytest

from icopa_core.task_executor.vm_general_actions.vm_general_containers_loader import (
    PresetContainersError,
    is_preset_containers_fallback_enabled,
    load_preset_containers,
    resolve_preset_container,
)


def test_resolve_preset_container_matches_id_alias_and_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "Preset_Containers.yaml"
    config_path.write_text(
        """
- id: stress_cpu_ram
  name: stress_container
  aliases: [legacy-stress]
  image: example.invalid/icopa/icopa-stress-container:v0.1
  run_args: --network host
  parameters:
    cpu_cores: 2
    mem_gb: 1
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ICOPA_PRESET_CONTAINERS_PATH", str(config_path))

    assert resolve_preset_container("stress_cpu_ram") is not None
    assert resolve_preset_container("legacy-stress") is not None
    assert resolve_preset_container("stress_container") is not None


def test_load_preset_containers_rejects_invalid_parameters_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "Preset_Containers.yaml"
    config_path.write_text(
        """
- id: stress_cpu_ram
  name: stress_container
  image: example.invalid/icopa/icopa-stress-container:v0.1
  parameters: bad
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ICOPA_PRESET_CONTAINERS_PATH", str(config_path))

    with pytest.raises(PresetContainersError, match="parameters"):
        load_preset_containers()


def test_fallback_toggle_defaults_to_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", raising=False)
    assert is_preset_containers_fallback_enabled() is True

    monkeypatch.setenv("ICOPA_ENABLE_PRESET_CONTAINERS_FALLBACK", "false")
    assert is_preset_containers_fallback_enabled() is False
