"""Minimal Kubernetes client helpers used by orchestration code."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import shlex
import subprocess
from typing import Any


class K8sClient:
    """Small wrapper around kubectl with inventory-networking helpers."""

    def __init__(
        self,
        kubeconfig: str | None = None,
        namespace: str = "default",
        kubectl_bin: str = "kubectl",
    ) -> None:
        self.kubeconfig = kubeconfig
        self.namespace = namespace
        self.kubectl_bin = kubectl_bin

    def execute_command(
        self,
        command: Sequence[str] | str,
        *,
        check: bool = False,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a kubectl command and return the completed process."""
        cmd = self._build_kubectl_command(command)
        return subprocess.run(
            cmd,
            check=check,
            timeout=timeout,
            text=True,
            capture_output=True,
        )

    def check_connection(self) -> bool:
        """Return True when kubectl can reach the configured cluster."""
        result = self.execute_command(["cluster-info"])
        return result.returncode == 0

    def resolve_zenoh_router_port(
        self,
        networking: Mapping[str, Any] | None,
        default_port: int = 7447,
    ) -> int:
        """Resolve zenoh router TCP port from host networking JSON."""
        if not isinstance(networking, Mapping):
            return default_port

        explicit = networking.get("zenohRouterPort")
        if isinstance(explicit, int):
            return explicit
        if isinstance(explicit, str) and explicit.isdigit():
            return int(explicit)

        for item in self._open_ports(networking):
            if not isinstance(item, Mapping):
                continue
            proto = str(item.get("proto", "")).lower()
            purpose = str(item.get("purpose", "")).lower()
            port = item.get("port")
            if proto == "tcp" and "zenoh" in purpose and "router" in purpose:
                parsed = _parse_port(port)
                if parsed is not None:
                    return parsed

        return default_port

    def _build_kubectl_command(self, command: Sequence[str] | str) -> list[str]:
        args = shlex.split(command) if isinstance(command, str) else list(command)
        cmd = [self.kubectl_bin]
        if self.kubeconfig:
            cmd.extend(["--kubeconfig", self.kubeconfig])
        if self.namespace:
            cmd.extend(["-n", self.namespace])
        cmd.extend(args)
        return cmd

    @staticmethod
    def _open_ports(networking: Mapping[str, Any]) -> list[Any]:
        open_ports = networking.get("openPorts")
        if isinstance(open_ports, list):
            return open_ports
        firewall = networking.get("firewall")
        if isinstance(firewall, Mapping) and isinstance(firewall.get("openPorts"), list):
            return firewall["openPorts"]
        return []


def _parse_port(value: Any) -> int | None:
    if isinstance(value, int) and 1 <= value <= 65535:
        return value
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        if 1 <= parsed <= 65535:
            return parsed
    return None


# Backward-compatible alias kept for existing imports.
K8s_Client = K8sClient
