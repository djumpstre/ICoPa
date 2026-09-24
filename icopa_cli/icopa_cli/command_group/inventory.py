"""Command group for inventory operations."""

from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path

from tabulate import tabulate
import yaml

from icopa_cli.icopa_config import IcopaConfig
from icopa_cli.command_group.base import CommandGroupBase
from icopa_cli.endpoints import Endpoints


INVENTORY_HELP = '''
ICoPa CLI [inv] command group for inventory management: SSH, Kubernetes RBAC

Usage:
    icopa inv <command> [-args]

Commands:
    list                List imported VMs
    upload              Upload inventory data from a YAML file
    info                Show detailed information for one inventory resource
    delete              Delete one inventory resource
    check_vm            Check one VM's SSH connectivity by VM name
    stop_container      Stop and remove one container on a VM by vm id or vm name

Resource pattern (for future expansion):
    vm/<vm_name>
'''


class InventoryCommandGroup(CommandGroupBase):
    """Command group [inv]."""

    COMMAND_LIST = [
        "list",
        "upload",
        "info",
        "delete",
        "check_vm",
        "stop_container",
    ]

    def __init__(self, subparsers) -> None:
        super().__init__(subparsers, "inv")
        self.init_subcommand_upload()
        self.init_subcommand_info()
        self.init_subcommand_delete()
        self.init_subcommand_check_vm()
        self.init_subcommand_stop_container()

    def init_subcommand_upload(self):
        parser = self.commands["upload"]
        parser.add_argument("--file", required=True, help="Path to inventory YAML file")

    def init_subcommand_info(self):
        parser = self.commands["info"]
        parser.add_argument("resource", nargs="?", help="Resource selector, e.g. vm/cloud_vm")
        parser.add_argument("--id", dest="resource_id", type=int, help="Inventory VM id")

    def init_subcommand_delete(self):
        parser = self.commands["delete"]
        parser.add_argument("resource", help="Resource selector, e.g. vm/cloud_vm")

    def init_subcommand_check_vm(self):
        parser = self.commands["check_vm"]
        parser.add_argument("name", help="VM name from inventory")

    def init_subcommand_stop_container(self):
        parser = self.commands["stop_container"]
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument("--vm_id", type=int, help="VM id from inventory")
        target.add_argument("--vm", help="VM name from inventory")
        parser.add_argument("--container", required=True, help="Container name to stop/remove, e.g. icopa-zenoh-probe-responder")

    def _get_context(self):
        return IcopaConfig.get_current_config()

    def _parse_vm_resource(self, resource: str) -> str:
        if not resource.startswith("vm/"):
            raise ValueError("Only vm/<vm_name> is supported right now.")
        vm_name = resource.split("/", 1)[1].strip()
        if not vm_name:
            raise ValueError("VM name is required. Use vm/<vm_name>.")
        return vm_name

    @staticmethod
    def _resolve_vm_name_from_id(resource_id: int, inventory_items: list) -> str:
        for item in inventory_items:
            if not isinstance(item, dict):
                continue
            if item.get("id") != resource_id:
                continue
            vm_name = str(item.get("name") or "").strip()
            if vm_name:
                return vm_name
            break
        raise ValueError(f"Inventory VM id {resource_id} was not found.")

    @staticmethod
    def _gpu_type_label(system_info: dict) -> str:
        gpu_entries = system_info.get("gpu") if isinstance(system_info, dict) else []
        if not isinstance(gpu_entries, list) or not gpu_entries:
            return "-"
        labels: list[str] = []
        for raw in gpu_entries:
            line = str(raw).strip()
            if not line:
                continue
            if "," in line:
                # nvidia-smi CSV output: "name, memory.total, driver"
                label = line.split(",", 1)[0].strip()
            elif ": " in line:
                # lspci-like output: "<slot> ... controller: <gpu type>"
                label = line.rsplit(": ", 1)[-1].strip()
            else:
                label = line
            if label and label not in labels:
                labels.append(label)
        return "; ".join(labels) if labels else "-"

    @staticmethod
    def _container_names_label(containers: list) -> str:
        if not isinstance(containers, list) or not containers:
            return "-"
        names: list[str] = []
        for item in containers:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
            else:
                name = str(item).strip()
            if name and name not in names:
                names.append(name)
        return ", ".join(names) if names else "-"

    @staticmethod
    def _print_container_status_details(container_status: list) -> None:
        print("Container Status:")
        if not isinstance(container_status, list) or not container_status:
            print("(none)")
            return

        rows = []
        for item in container_status:
            if not isinstance(item, dict):
                continue
            rows.append(
                [
                    item.get("name", "-"),
                    item.get("state", "-"),
                    item.get("status", ""),
                    item.get("id", ""),
                    item.get("image", ""),
                    item.get("ports", ""),
                    item.get("running_for", ""),
                ]
            )
        if not rows:
            print("(none)")
            return

        print(
            tabulate(
                rows,
                headers=["NAME", "STATE", "STATUS", "ID", "IMAGE", "PORTS", "RUNNING_FOR"],
                tablefmt="github",
            )
        )

    def _print_vm_info(self, vm: dict):
        credential = vm.get("credential") or {}
        metadata = vm.get("metadata") or {}
        networking = vm.get("networking") or {}
        system_info = vm.get("system_info") or {}
        managed_containers = vm.get("managed_containers") or []
        container_status = metadata.get("managed_container_status") or []

        cpu_cores = system_info.get("cpu_cores")
        mem_total_gb = system_info.get("mem_total_gb")
        gpu_type = self._gpu_type_label(system_info)

        print(f"Name:              {vm.get('name', '')}")
        print(f"Address:           {vm.get('address', '')}")
        print(f"SSH User:          {vm.get('user_name', '')}")
        print(f"Port:              {vm.get('port', '')}")
        print(f"Status:            {vm.get('status', '')}")
        print(f"Credential:        {credential.get('name', '')}")
        print(f"Last Connection:   {vm.get('last_connection_time', '')}")
        print(f"Container Runtime: {vm.get('container_runtime_type', '')}")
        print(f"Runtime Ready:     {vm.get('container_runtime_ready', '')}")
        print(f"Cluster:           {vm.get('cluster_membership', '')}")
        print(f"CPU Cores:         {cpu_cores if cpu_cores is not None else '-'}")
        print(f"Memory (GB):       {mem_total_gb if mem_total_gb is not None else '-'}")
        print(f"GPU Type:          {gpu_type}")
        print(f"Managed Containers:{self._container_names_label(managed_containers)}")
        print("")
        print("Managed Container Status:")
        self._print_container_status_details(container_status)
        print("")
        print("System Info:")
        print(json.dumps(system_info, indent=2, sort_keys=True))
        print("")
        print("Metadata:")
        print(json.dumps(metadata, indent=2, sort_keys=True))
        print("")
        print("Networking:")
        print(json.dumps(networking, indent=2, sort_keys=True))

    def _print_vm_check(self, result: dict):
        system_info = result.get("system_info") or {}
        cpu_cores = system_info.get("cpu_cores")
        mem_total_gb = system_info.get("mem_total_gb")
        gpu_type = self._gpu_type_label(system_info)
        managed_containers = result.get("managed_containers") or []
        container_status = result.get("managed_container_status") or []
        print(f"Name:              {result.get('name', '')}")
        print(f"Address:           {result.get('address', '')}")
        print(f"Port:              {result.get('port', '')}")
        print(f"Connectivity:      {'OK' if result.get('ok') else 'FAILED'}")
        print(f"SSH Connectivity:  {'OK' if result.get('ssh_ok') else 'FAILED'}")
        print(f"Docker Runtime:    {'READY' if result.get('docker_ok') else 'NOT_READY'}")
        print(f"Runtime Type:      {result.get('container_runtime_type', '')}")
        print(f"Runtime Ready:     {result.get('container_runtime_ready', '')}")
        print(f"Return Code:       {result.get('returncode', '')}")
        print(f"Stdout:            {result.get('stdout', '')}")
        print(f"Stderr:            {result.get('stderr', '')}")
        print(f"CPU Cores:         {cpu_cores if cpu_cores is not None else '-'}")
        print(f"Memory (GB):       {mem_total_gb if mem_total_gb is not None else '-'}")
        print(f"GPU Type:          {gpu_type}")
        print(f"Managed Containers:{self._container_names_label(managed_containers)}")
        self._print_container_status_details(container_status)

    def list(self):
        config = self._get_context()
        success, data = self.call_api(
            "GET",
            f"{config['server'].rstrip('/')}/{Endpoints.INVENTORY}",
            auth_token=config.get("token"),
        )
        if success:
            if isinstance(data, list) and data:
                rows = []
                for item in data:
                    system_info = item.get("system_info") or {}
                    cpu_cores = system_info.get("cpu_cores")
                    mem_total_gb = system_info.get("mem_total_gb")
                    gpu_type = self._gpu_type_label(system_info)
                    managed_containers = item.get("managed_containers") or []
                    rows.append(
                        [
                            item.get("id"),
                            item.get("name"),
                            item.get("address"),
                            item.get("user_name"),
                            item.get("status"),
                            cpu_cores if cpu_cores is not None else "-",
                            mem_total_gb if mem_total_gb is not None else "-",
                            gpu_type,
                            self._container_names_label(managed_containers),
                            (item.get("credential") or {}).get("name", ""),
                        ]
                    )
                print(
                    tabulate(
                        rows,
                        headers=[
                            "ID",
                            "NAME",
                            "ADDRESS",
                            "SSH_USER",
                            "STATUS",
                            "CPU",
                            "RAM_GB",
                            "GPU_TYPE",
                            "CONTAINERS",
                            "SSH_KEY",
                        ],
                        tablefmt="github",
                    )
                )
            else:
                print(data)

    def upload(self, *args):
        parser = self.commands["upload"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        with ExitStack() as stack:
            inventory_path = Path(parsed.file)
            inventory_handle = stack.enter_context(open(inventory_path, "rb"))
            files = {"file": (str(inventory_path), inventory_handle, "application/x-yaml")}

            payload = yaml.safe_load(inventory_path.read_text(encoding="utf-8")) or {}
            body = payload.get("spec") if isinstance(payload.get("spec"), dict) else payload
            key_defs = body.get("ssh_key") if isinstance(body, dict) else []
            key_defs = key_defs if isinstance(key_defs, list) else []

            for key_item in key_defs:
                if not isinstance(key_item, dict):
                    continue
                key_name = key_item.get("name")
                key_path = key_item.get("key_path")
                if not key_name or not key_path:
                    continue
                if not os.path.exists(key_path):
                    print(f"[WARN] SSH key file not found locally, skipped upload for '{key_name}': {key_path}")
                    continue

                key_handle = stack.enter_context(open(key_path, "rb"))
                files[f"ssh_key_file__{key_name}"] = (
                    os.path.basename(key_path),
                    key_handle,
                    "application/octet-stream",
                )

            success, data = self.call_api(
                "POST",
                f"{config['server'].rstrip('/')}/{Endpoints.INVENTORY}",
                files=files,
                auth_token=config.get("token"),
            )

        if success:
            print(data)

    def info(self, *args):
        parser = self.commands["info"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        if parsed.resource and parsed.resource_id is not None:
            parser.error("Provide either vm/<vm_name> or --id, not both.")

        if parsed.resource_id is not None:
            success, inventory_items = self.call_api(
                "GET",
                f"{config['server'].rstrip('/')}/{Endpoints.INVENTORY}",
                auth_token=config.get("token"),
            )
            if not success or not isinstance(inventory_items, list):
                parser.error("Unable to resolve VM name from id.")
            try:
                vm_name = self._resolve_vm_name_from_id(parsed.resource_id, inventory_items)
            except ValueError as exc:
                parser.error(str(exc))
        else:
            if not parsed.resource:
                parser.error("Either vm/<vm_name> or --id is required.")
            try:
                vm_name = self._parse_vm_resource(parsed.resource)
            except ValueError as exc:
                parser.error(str(exc))
        success, data = self.call_api(
            "GET",
            f"{config['server'].rstrip('/')}/{Endpoints.INVENTORY_VM}{vm_name}/",
            auth_token=config.get("token"),
        )
        if success:
            self._print_vm_info(data)

    def delete(self, *args):
        parser = self.commands["delete"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        try:
            vm_name = self._parse_vm_resource(parsed.resource)
        except ValueError as exc:
            parser.error(str(exc))
        success, data = self.call_api(
            "DELETE",
            f"{config['server'].rstrip('/')}/{Endpoints.INVENTORY_VM}{vm_name}/",
            auth_token=config.get("token"),
            expect_json=True,
        )
        if success:
            print(data)

    def check_vm(self, *args):
        parser = self.commands["check_vm"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        success, data = self.call_api(
            "POST",
            f"{config['server'].rstrip('/')}/{Endpoints.INVENTORY_CHECK_VM}",
            json_data={"name": parsed.name},
            auth_token=config.get("token"),
        )
        if success:
            self._print_vm_check(data)

    def stop_container(self, *args):
        parser = self.commands["stop_container"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        payload = {
            "container": parsed.container,
        }
        if parsed.vm_id is not None:
            payload["vm_id"] = parsed.vm_id
        if parsed.vm:
            payload["vm"] = parsed.vm

        success, data = self.call_api(
            "POST",
            f"{config['server'].rstrip('/')}/{Endpoints.INVENTORY_STOP_CONTAINER}",
            json_data=payload,
            auth_token=config.get("token"),
        )
        if not success:
            return
        if not isinstance(data, dict):
            print(data)
            return

        print(f"VM ID:             {data.get('vm_id', '')}")
        print(f"VM Name:           {data.get('vm', '')}")
        print(f"Container:         {data.get('container', '')}")
        print(f"Result:            {'OK' if data.get('ok') else 'FAILED'}")
        print(f"State:             {data.get('state', '')}")
        print(f"Message:           {data.get('message', '')}")
        error = str(data.get("error") or data.get("stop_stderr") or "").strip()
        if error:
            print(f"Error:             {error}")

    def print_help(self):
        print(INVENTORY_HELP)
