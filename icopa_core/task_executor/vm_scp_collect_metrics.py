"""Collect metrics artifacts from VMs over SCP."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from icopa_core.connectors.vm_ssh_client import build_vm_ssh_client


@dataclass(frozen=True)
class SCPCollectTarget:
    """One remote VM source for SCP metrics collection."""

    node_name: str
    host: str
    username: str
    port: int = 22
    key_path: str | None = None


def _collect_trace(event: str, **meta: Any) -> None:
    line = f"[collect-metrics] {event}"
    if meta:
        line = f"{line} | {meta}"
    print(line, flush=True)


def _list_local_files(base_dir: Path) -> list[str]:
    if not base_dir.exists():
        return []
    files: list[str] = []
    for path in sorted(base_dir.rglob("*")):
        if path.is_file():
            files.append(str(path.relative_to(base_dir)))
    return files


def _scp_pull_dir(
    *,
    target: SCPCollectTarget,
    remote_dir: str,
    local_dir: Path,
    connect_timeout_sec: int,
    timeout_sec: int,
) -> dict[str, Any]:
    _collect_trace(
        "scp_start",
        node=target.node_name,
        host=target.host,
        remote_dir=remote_dir,
        local_dir=str(local_dir),
    )
    local_dir.mkdir(parents=True, exist_ok=True)
    vm_payload = {
        "address": target.host,
        "user_name": target.username,
        "port": int(target.port),
        "key_path": str(target.key_path or ""),
    }
    try:
        ssh_client = build_vm_ssh_client(
            vm_payload,
            action={"connect_timeout_sec": int(connect_timeout_sec)},
            default_connect_timeout=int(connect_timeout_sec),
        )
    except ValueError as exc:
        _collect_trace("scp_failed_invalid_target", node=target.node_name, error=str(exc))
        return {
            "ok": False,
            "returncode": 2,
            "stdout": "",
            "stderr": str(exc),
            "local_dir": str(local_dir),
            "files": [],
        }
    scp_result = ssh_client.scp_pull_directory(
        remote_dir=remote_dir,
        local_dir=str(local_dir),
        timeout=int(timeout_sec),
    )
    result = {
        "ok": scp_result.success,
        "returncode": scp_result.returncode,
        "stdout": scp_result.stdout,
        "stderr": scp_result.stderr,
        "local_dir": str(local_dir),
        "files": _list_local_files(local_dir),
    }
    _collect_trace(
        "scp_finished",
        node=target.node_name,
        ok=result["ok"],
        returncode=result["returncode"],
        file_count=len(result.get("files") or []),
        local_dir=result["local_dir"],
    )
    return result


class VMScpCollectMetricsExec:
    """SCP-based metrics collector for one or more VM targets."""

    action_type = "collect_metrics:scp"

    def __init__(
        self,
        *,
        targets: list[SCPCollectTarget],
        remote_dir: str,
        local_base_dir: str,
        connect_timeout_sec: int = 8,
        timeout_sec: int = 45,
    ) -> None:
        self.targets = targets
        self.remote_dir = str(remote_dir or "").strip()
        self.local_base_dir = str(local_base_dir or "").strip()
        self.connect_timeout_sec = connect_timeout_sec
        self.timeout_sec = timeout_sec

    @staticmethod
    def _as_positive_int(value: Any, field_name: str) -> int:
        try:
            parsed = int(str(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"'{field_name}' must be a positive integer.") from exc
        if parsed <= 0:
            raise ValueError(f"'{field_name}' must be a positive integer.")
        return parsed

    def validate_input(self) -> None:
        if not isinstance(self.targets, list) or not self.targets:
            raise ValueError("'targets' must be a non-empty list of SCPCollectTarget.")
        if not self.remote_dir:
            raise ValueError("'remote_dir' must be a non-empty string.")
        if not self.local_base_dir:
            raise ValueError("'local_base_dir' must be a non-empty string.")
        self.connect_timeout_sec = self._as_positive_int(self.connect_timeout_sec, "connect_timeout_sec")
        self.timeout_sec = self._as_positive_int(self.timeout_sec, "timeout_sec")

    def execute(self) -> dict[str, Any]:
        self.validate_input()
        base_dir = Path(self.local_base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        _collect_trace(
            "collect_started",
            remote_dir=self.remote_dir,
            backend_base_dir=str(base_dir),
            targets=[item.node_name for item in self.targets],
        )

        by_target: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        overall_ok = True
        for target in self.targets:
            result = _scp_pull_dir(
                target=target,
                remote_dir=self.remote_dir,
                local_dir=base_dir / target.node_name,
                connect_timeout_sec=self.connect_timeout_sec,
                timeout_sec=self.timeout_sec,
            )
            by_target[target.node_name] = result
            if not result.get("ok"):
                overall_ok = False
                errors.append(
                    f"Failed to SCP metrics from node '{target.node_name}' ({target.username}@{target.host}). "
                    f"stderr={result.get('stderr', '')}"
                )
        payload = {
            "ok": overall_ok,
            "remote_dir": self.remote_dir,
            "local_base_dir": str(base_dir),
            "targets": by_target,
            "errors": errors,
        }
        _collect_trace(
            "collect_finished",
            ok=overall_ok,
            backend_base_dir=str(base_dir),
            target_count=len(by_target),
            error_count=len(errors),
        )
        return payload


def collect_metrics_from_vms(
    *,
    targets: list[SCPCollectTarget],
    remote_dir: str,
    local_base_dir: str,
    connect_timeout_sec: int = 8,
    timeout_sec: int = 45,
) -> dict[str, Any]:
    """Pull metrics directory from each target VM into local_base_dir/<node_name>/."""
    return VMScpCollectMetricsExec(
        targets=targets,
        remote_dir=remote_dir,
        local_base_dir=local_base_dir,
        connect_timeout_sec=connect_timeout_sec,
        timeout_sec=timeout_sec,
    ).execute()
