from pathlib import Path

import yaml

from icopa_cli.icopa_config import IcopaConfig


def test_nested_config_is_private_and_omits_password(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "account" / "config"
    monkeypatch.setenv("ICOPA_CONFIG", str(path))
    IcopaConfig.update_config({"server": "http://localhost:8000", "password": "unused"})
    assert path.stat().st_mode & 0o777 == 0o600
    assert "password" not in yaml.safe_load(path.read_text())
    assert IcopaConfig.get_current_config()["server"] == "http://localhost:8000"


def test_existing_config_permissions_are_restricted(tmp_path, monkeypatch):
    path = Path(tmp_path) / "config"
    path.write_text("server: http://localhost:8000\npassword: unused\n")
    path.chmod(0o644)
    monkeypatch.setenv("ICOPA_CONFIG", str(path))
    IcopaConfig.update_config({"user": "researcher"})
    assert path.stat().st_mode & 0o777 == 0o600
    assert "password" not in yaml.safe_load(path.read_text())
