"""GCP provider implementation."""


class GCPProvisioner:
    """GCP provisioner implementation."""

    def __init__(self, credential: dict):
        self.credential = credential

    def provision_vm(self, vm_config: dict) -> dict:
        """Provision a VM on GCP."""
