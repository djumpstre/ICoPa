"""
VM SSH client utilities for connectivity checks and remote command execution.
TODO: Add all utilities implmeneted in this script.
 - Check connectivity via ssh connection 
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class VMSSHResult:
    """Result for VM SSH command execution."""

    success: bool
    returncode: int
    stdout: str
    stderr: str


@dataclass
class VMContainerStatus:
    """Container status resolved on a remote host."""

    name: str
    exists: bool
    running: bool
    status: str


@dataclass
class VMContainerOperationResult:
    """Container lifecycle operation result."""

    ok: bool
    state: str
    message: str
    error: str = ""
    stop_stdout: str = ""
    stop_stderr: str = ""
    remove_stdout: str = ""
    remove_stderr: str = ""


class VMSSHClient:
    """Thin wrapper around the local ssh binary for VM access."""

    def __init__(
        self,
        host: str,
        username: str,
        port: int = 22,
        key_path: str | None = None,
        connect_timeout: int = 8,
    ) -> None:
        self.host = host
        self.username = username
        self.port = int(port)
        self.key_path = key_path
        self.connect_timeout = int(connect_timeout)

    def _base_args(self) -> list[str]:
        args = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
            "-p",
            str(self.port),
        ]
        if self.key_path:
            args.extend(["-i", self.key_path])
        args.append(f"{self.username}@{self.host}")
        return args

    def _scp_base_args(self) -> list[str]:
        args = [
            "scp",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
            "-P",
            str(self.port),
        ]
        if self.key_path:
            args.extend(["-i", self.key_path])
        return args

    @staticmethod
    def _validate_container_name(container_name: str) -> str:
        name = str(container_name or "").strip()
        if not name:
            raise ValueError("Container name is required.")
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$", name):
            raise ValueError(f"Invalid container name: {container_name}")
        return name

    def execute_command(self, command: str, timeout: int = 20) -> VMSSHResult:
        """Execute a command on the target host over SSH."""
        if self.key_path and not os.path.exists(self.key_path):
            return VMSSHResult(
                success=False,
                returncode=2,
                stdout="",
                stderr=f"SSH key path does not exist: {self.key_path}",
            )

        args = [*self._base_args(), command]
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except subprocess.TimeoutExpired:
            return VMSSHResult(
                success=False,
                returncode=124,
                stdout="",
                stderr=f"SSH command timed out after {timeout} seconds.",
            )
        except FileNotFoundError:
            return VMSSHResult(
                success=False,
                returncode=127,
                stdout="",
                stderr="ssh binary not found in runtime.",
            )

        return VMSSHResult(
            success=proc.returncode == 0,
            returncode=proc.returncode,
            stdout=(proc.stdout or "").strip(),
            stderr=(proc.stderr or "").strip(),
        )

    def scp_pull_directory(
        self,
        *,
        remote_dir: str,
        local_dir: str,
        timeout: int = 45,
    ) -> VMSSHResult:
        """Copy one remote directory into ``local_dir`` using SCP."""
        if self.key_path and not os.path.exists(self.key_path):
            return VMSSHResult(
                success=False,
                returncode=2,
                stdout="",
                stderr=f"SSH key path does not exist: {self.key_path}",
            )
        remote_path = str(remote_dir or "").strip()
        if not remote_path:
            return VMSSHResult(
                success=False,
                returncode=2,
                stdout="",
                stderr="remote_dir is required for SCP pull.",
            )
        local_path = Path(str(local_dir or "").strip())
        if not str(local_path):
            return VMSSHResult(
                success=False,
                returncode=2,
                stdout="",
                stderr="local_dir is required for SCP pull.",
            )
        local_path.mkdir(parents=True, exist_ok=True)
        remote_spec = f"{self.username}@{self.host}:{remote_path}/."
        args = [*self._scp_base_args(), "-r", remote_spec, str(local_path)]
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except subprocess.TimeoutExpired:
            return VMSSHResult(
                success=False,
                returncode=124,
                stdout="",
                stderr=f"SCP command timed out after {timeout} seconds.",
            )
        except FileNotFoundError:
            return VMSSHResult(
                success=False,
                returncode=127,
                stdout="",
                stderr="scp binary not found in runtime.",
            )
        return VMSSHResult(
            success=proc.returncode == 0,
            returncode=proc.returncode,
            stdout=(proc.stdout or "").strip(),
            stderr=(proc.stderr or "").strip(),
        )

    def test_connectivity(self, check_container_runtime: bool = True) -> VMSSHResult:
        """
        Validate SSH connectivity and optionally verify container runtime access.
        """
        ssh_result = self.execute_command("echo icopa_ssh_ok", timeout=self.connect_timeout + 5)
        if not ssh_result.success or not check_container_runtime:
            return ssh_result

        docker_result = self.check_docker_runtime()
        if docker_result.success and docker_result.stdout.strip() == "docker_ok":
            return ssh_result

        return VMSSHResult(
            success=False,
            returncode=docker_result.returncode,
            stdout=ssh_result.stdout,
            stderr=docker_result.stderr or "Docker runtime is not ready on target VM.",
        )

    def close_connection(self) -> None:
        """Kept for interface compatibility; subprocess mode has no persistent connection."""
        return None

    def check_docker_runtime(self) -> VMSSHResult:
        """Check if Docker is installed and can be run without sudo."""
        return self.execute_command(
            "docker info > /dev/null 2>&1 && echo docker_ok || echo docker_error",
            timeout=15,
        )

    def list_running_containers(self, timeout: int = 20) -> list[dict[str, str]]:
        """Collect all running containers from docker ps with structured fields."""
        result = self.execute_command("docker ps --format '{{json .}}'", timeout=timeout)
        if not result.success:
            return []

        containers: list[dict[str, str]] = []
        for raw_line in (result.stdout or "").splitlines():
            line = str(raw_line or "").strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            name = str(item.get("Names") or "").strip()
            if not name:
                continue
            containers.append(
                {
                    "name": name,
                    "id": str(item.get("ID") or "").strip(),
                    "image": str(item.get("Image") or "").strip(),
                    "command": str(item.get("Command") or "").strip(),
                    "created_at": str(item.get("CreatedAt") or "").strip(),
                    "running_for": str(item.get("RunningFor") or "").strip(),
                    "ports": str(item.get("Ports") or "").strip(),
                    "status": str(item.get("Status") or "").strip(),
                }
            )
        return containers

    def get_container_status(self, container_name: str, timeout: int = 20) -> VMContainerStatus:
        """Resolve whether a container exists and if it is currently running."""
        name = self._validate_container_name(container_name)
        running_result = self.execute_command(
            f"docker ps --filter \"name=^/{name}$\" --format '{{{{.Names}}}}|{{{{.Status}}}}'",
            timeout=timeout,
        )
        running_lines = [line for line in (running_result.stdout or "").splitlines() if line.strip()]
        if running_result.success and running_lines:
            return VMContainerStatus(name=name, exists=True, running=True, status=running_lines[0].strip())

        all_result = self.execute_command(
            f"docker ps -a --filter \"name=^/{name}$\" --format '{{{{.Names}}}}|{{{{.Status}}}}'",
            timeout=timeout,
        )
        all_lines = [line for line in (all_result.stdout or "").splitlines() if line.strip()]
        if all_result.success and all_lines:
            return VMContainerStatus(name=name, exists=True, running=False, status=all_lines[0].strip())

        return VMContainerStatus(name=name, exists=False, running=False, status="")

    def stop_container(self, container_name: str, timeout: int = 30) -> VMContainerOperationResult:
        """Stop one container if it is currently running."""
        status = self.get_container_status(container_name, timeout=timeout)
        if not status.exists:
            return VMContainerOperationResult(
                ok=False,
                state="not_found",
                message=f"Container '{status.name}' was not found.",
            )
        if not status.running:
            return VMContainerOperationResult(
                ok=True,
                state="already_stopped",
                message=f"Container '{status.name}' is already stopped.",
            )

        stop_result = self.execute_command(f"docker stop {status.name}", timeout=timeout)
        if not stop_result.success:
            return VMContainerOperationResult(
                ok=False,
                state="stop_failed",
                message=f"Failed to stop container '{status.name}'.",
                error=stop_result.stderr,
                stop_stdout=stop_result.stdout,
                stop_stderr=stop_result.stderr,
            )
        return VMContainerOperationResult(
            ok=True,
            state="stopped",
            message=f"Container '{status.name}' is stopped.",
            stop_stdout=stop_result.stdout,
            stop_stderr=stop_result.stderr,
        )

    def remove_container(self, container_name: str, timeout: int = 30) -> VMContainerOperationResult:
        """Remove one container if it exists."""
        status = self.get_container_status(container_name, timeout=timeout)
        if not status.exists:
            return VMContainerOperationResult(
                ok=False,
                state="not_found",
                message=f"Container '{status.name}' was not found.",
            )

        remove_result = self.execute_command(f"docker rm {status.name}", timeout=timeout)
        if not remove_result.success:
            return VMContainerOperationResult(
                ok=False,
                state="remove_failed",
                message=f"Failed to remove container '{status.name}'.",
                error=remove_result.stderr,
                remove_stdout=remove_result.stdout,
                remove_stderr=remove_result.stderr,
            )
        return VMContainerOperationResult(
            ok=True,
            state="removed",
            message=f"Container '{status.name}' is removed.",
            remove_stdout=remove_result.stdout,
            remove_stderr=remove_result.stderr,
        )

    def stop_and_remove_container(self, container_name: str, timeout: int = 30) -> VMContainerOperationResult:
        """Stop (if needed) and remove one container by name."""
        status = self.get_container_status(container_name, timeout=timeout)
        if not status.exists:
            return VMContainerOperationResult(
                ok=False,
                state="not_found",
                message=f"Container '{status.name}' was not found.",
            )

        stop_stdout = ""
        stop_stderr = ""
        if status.running:
            stop_result = self.execute_command(f"docker stop {status.name}", timeout=timeout)
            stop_stdout = stop_result.stdout
            stop_stderr = stop_result.stderr
            if not stop_result.success:
                return VMContainerOperationResult(
                    ok=False,
                    state="stop_failed",
                    message=f"Failed to stop container '{status.name}'.",
                    error=stop_result.stderr,
                    stop_stdout=stop_result.stdout,
                    stop_stderr=stop_result.stderr,
                )

        remove_result = self.execute_command(f"docker rm {status.name}", timeout=timeout)
        if not remove_result.success:
            return VMContainerOperationResult(
                ok=False,
                state="remove_failed",
                message=f"Failed to remove container '{status.name}'.",
                error=remove_result.stderr,
                stop_stdout=stop_stdout,
                stop_stderr=stop_stderr,
                remove_stdout=remove_result.stdout,
                remove_stderr=remove_result.stderr,
            )
        return VMContainerOperationResult(
            ok=True,
            state="removed",
            message=f"Container '{status.name}' is stopped and removed.",
            stop_stdout=stop_stdout,
            stop_stderr=stop_stderr,
            remove_stdout=remove_result.stdout,
            remove_stderr=remove_result.stderr,
        )
    

class VM_SSH_Client(VMSSHClient):
    """Backward-compatible alias for earlier naming style."""


# Backward-compatibility aliases.
SSHResult = VMSSHResult
SSHClient = VMSSHClient
SSH_Client = VM_SSH_Client


def resolve_vm_ssh_key_path(vm_payload: dict[str, Any], workdir: str = "") -> str:
    """Resolve absolute key path from one inventory VM payload."""
    key_path = (
        vm_payload.get("key_path")
        or vm_payload.get("ssh_key_path")
        or (vm_payload.get("credential") or {}).get("key_path")
        or vm_payload.get("credential_key_path")
        or (vm_payload.get("credential") or {}).get("key_file")
    )
    if not key_path:
        raise ValueError("Target VM does not include an SSH key path.")
    key_path_text = str(key_path)
    if key_path_text.startswith("http://") or key_path_text.startswith("https://"):
        raise ValueError("Target VM SSH key path must be a local filesystem path.")
    key_path_obj = Path(key_path_text)
    if not key_path_obj.is_absolute() and workdir:
        key_path_obj = Path(workdir) / key_path_obj
    return str(key_path_obj)


def build_vm_ssh_client(
    vm_payload: dict[str, Any],
    *,
    action: dict[str, Any] | None = None,
    workdir: str = "",
    default_connect_timeout: int = 8,
) -> VMSSHClient:
    """Build ``VMSSHClient`` from one inventory VM payload."""
    host = str(vm_payload.get("address") or "").strip()
    if not host:
        raise ValueError("Target VM is missing required field 'address'.")
    username = str(vm_payload.get("user_name") or vm_payload.get("username") or "").strip()
    if not username:
        raise ValueError("Target VM is missing required field 'user_name'.")
    try:
        port = int(vm_payload.get("port") or 22)
    except (TypeError, ValueError) as exc:
        raise ValueError("Target VM field 'port' must be an integer.") from exc
    key_path = resolve_vm_ssh_key_path(vm_payload, workdir=workdir)
    connect_timeout = default_connect_timeout
    if isinstance(action, dict):
        try:
            connect_timeout = int(action.get("connect_timeout_sec") or default_connect_timeout)
        except (TypeError, ValueError):
            connect_timeout = default_connect_timeout
    return VMSSHClient(
        host=host,
        username=username,
        port=port,
        key_path=key_path,
        connect_timeout=connect_timeout,
    )
