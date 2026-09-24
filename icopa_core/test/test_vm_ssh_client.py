from __future__ import annotations

from icopa_core.connectors.vm_ssh_client import VMSSHClient, VMSSHResult


def _ok(stdout: str = "") -> VMSSHResult:
    return VMSSHResult(success=True, returncode=0, stdout=stdout, stderr="")


def test_ssh_and_scp_reject_changed_host_keys() -> None:
    client = VMSSHClient(host="127.0.0.1", username="ubuntu")
    for args in (client._base_args(), client._scp_base_args()):
        assert "StrictHostKeyChecking=accept-new" in args
        assert "StrictHostKeyChecking=no" not in args


def test_stop_and_remove_container_running_success(monkeypatch) -> None:
    client = VMSSHClient(host="127.0.0.1", username="ubuntu", key_path="/tmp/id_rsa")
    commands: list[str] = []

    def _fake_execute(command: str, timeout: int = 20) -> VMSSHResult:
        commands.append(command)
        if command == "docker ps --filter \"name=^/icopa-test$\" --format '{{.Names}}|{{.Status}}'":
            return _ok("icopa-test|Up 10 seconds\n")
        if command == "docker stop icopa-test":
            return _ok("icopa-test\n")
        if command == "docker rm icopa-test":
            return _ok("icopa-test\n")
        return _ok("")

    monkeypatch.setattr(client, "execute_command", _fake_execute)

    result = client.stop_and_remove_container("icopa-test")
    assert result.ok is True
    assert result.state == "removed"
    assert "docker stop icopa-test" in commands
    assert "docker rm icopa-test" in commands


def test_stop_and_remove_container_stopped_success(monkeypatch) -> None:
    client = VMSSHClient(host="127.0.0.1", username="ubuntu", key_path="/tmp/id_rsa")
    commands: list[str] = []

    def _fake_execute(command: str, timeout: int = 20) -> VMSSHResult:
        commands.append(command)
        if command == "docker ps --filter \"name=^/icopa-test$\" --format '{{.Names}}|{{.Status}}'":
            return _ok("")
        if command == "docker ps -a --filter \"name=^/icopa-test$\" --format '{{.Names}}|{{.Status}}'":
            return _ok("icopa-test|Exited (0) 1 minute ago\n")
        if command == "docker rm icopa-test":
            return _ok("icopa-test\n")
        return _ok("")

    monkeypatch.setattr(client, "execute_command", _fake_execute)

    result = client.stop_and_remove_container("icopa-test")
    assert result.ok is True
    assert result.state == "removed"
    assert "docker stop icopa-test" not in commands
    assert "docker rm icopa-test" in commands
