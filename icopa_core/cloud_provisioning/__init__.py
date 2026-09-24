"""Cloud provisioner provider factory."""

from __future__ import annotations

from .azure import AzureProvisioner
from .base import CloudProvisioner


def get_provisioner(provider: str, credentials: dict) -> CloudProvisioner:
    provider_name = str(provider or "").strip().upper()
    if provider_name == "AZURE":
        return AzureProvisioner(credentials)
    raise ValueError(f"Unsupported cloud provider: {provider}")
