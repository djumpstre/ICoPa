"""Command group for cloud provisioning operations."""

from __future__ import annotations

import json
from pathlib import Path
import time

from tabulate import tabulate

from icopa_cli.command_group.base import CommandGroupBase
from icopa_cli.endpoints import Endpoints
from icopa_cli.icopa_config import IcopaConfig


CLOUD_HELP = """
ICoPa CLI [cloud] command group for cloud VM provisioning

Usage:
    icopa cloud <command> [-args]

Commands:
    upload --file <yaml>                       Upload VM artifacts and create INITIALIZED ProvisioningVM records
    create_vm --id <id>                        Start VM create process for one initialized record
    check --id <id>                            Trigger cloud check and wait for check result
    start_vm --id <id>                         Start one stopped cloud VM and wait for start result
    stop_vm --id <id>                          Stop one provisioned cloud VM and wait for stop result
    list [--provider azure]                    List cloud provisioning VMs (provider filter is optional)
    delete --id <id>                           Deprovision one cloud provisioning VM
"""


class CloudCommandGroup(CommandGroupBase):
    COMMAND_LIST = [
        "upload",
        "create_vm",
        "check",
        "start_vm",
        "stop_vm",
        "list",
        "delete",
    ]

    def __init__(self, subparsers) -> None:
        super().__init__(subparsers, "cloud")
        self.init_subcommand_upload()
        self.init_subcommand_create_vm()
        self.init_subcommand_check()
        self.init_subcommand_start_vm()
        self.init_subcommand_stop_vm()
        self.init_subcommand_list()
        self.init_subcommand_delete()

    def _get_context(self):
        return IcopaConfig.get_current_config()

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        normalized = str(provider or "").strip().upper()
        if normalized != "AZURE":
            raise ValueError(f"Provider '{provider}' will support soon.")
        return normalized

    def init_subcommand_upload(self):
        parser = self.commands["upload"]
        parser.add_argument("--file", required=True, help="Path to VM artifacts template YAML")

    def init_subcommand_create_vm(self):
        parser = self.commands["create_vm"]
        parser.add_argument("--id", required=True, type=int, help="Provisioning VM id to start create process")

    def init_subcommand_list(self):
        parser = self.commands["list"]
        parser.add_argument("--provider", required=False, default="", help="Optional provider filter, only azure is supported now")

    def init_subcommand_check(self):
        parser = self.commands["check"]
        parser.add_argument("--id", required=True, type=int, help="Provisioning VM id")
        parser.add_argument("--wait-seconds", type=int, default=60, help="Max seconds to wait for check result")

    def init_subcommand_delete(self):
        parser = self.commands["delete"]
        parser.add_argument("--id", required=True, type=int, help="Provisioning VM id")

    def init_subcommand_stop_vm(self):
        parser = self.commands["stop_vm"]
        parser.add_argument("--id", required=True, type=int, help="Provisioning VM id")
        parser.add_argument("--wait-seconds", type=int, default=90, help="Max seconds to wait for stop result")

    def init_subcommand_start_vm(self):
        parser = self.commands["start_vm"]
        parser.add_argument("--id", required=True, type=int, help="Provisioning VM id")
        parser.add_argument("--wait-seconds", type=int, default=90, help="Max seconds to wait for start result")

    def upload(self, *args):
        parser = self.commands["upload"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        artifact_path = Path(parsed.file)
        with open(artifact_path, "rb") as handle:
            success, data = self.call_api(
                "POST",
                f"{config['server'].rstrip('/')}/{Endpoints.CLOUD_VM_UPLOAD}",
                files={"file": (artifact_path.name, handle, "application/x-yaml")},
                auth_token=config.get("token"),
            )
        if success:
            print(json.dumps(data, indent=2, sort_keys=True))

    def create_vm(self, *args):
        parser = self.commands["create_vm"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        success, data = self.call_api(
            "POST",
            f"{config['server'].rstrip('/')}/{Endpoints.CLOUD_VMS}{parsed.id}/create/",
            json_data={},
            auth_token=config.get("token"),
        )
        if success:
            print(json.dumps(data, indent=2, sort_keys=True))

    def check(self, *args):
        parser = self.commands["check"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        success, data = self.call_api(
            "POST",
            f"{config['server'].rstrip('/')}/{Endpoints.CLOUD_VMS}{parsed.id}/check/",
            json_data={},
            auth_token=config.get("token"),
        )
        if not success:
            return
        check_request_id = int(data.get("check_request_id", 0))
        print(
            "Check dispatched:",
            f"provisioning_vm_id={data.get('provisioning_vm_id')}",
            f"check_request_id={check_request_id}",
            f"status={data.get('status')}",
            f"task_id={data.get('task_id')}",
        )
        if check_request_id <= 0:
            return

        deadline = time.time() + max(parsed.wait_seconds, 1)
        while time.time() < deadline:
            status_url = (
                f"{config['server'].rstrip('/')}/"
                f"{Endpoints.CLOUD_VM_CHECK_DETAIL.format(vm_id=parsed.id, check_id=check_request_id)}"
            )
            success_detail, detail = self.call_api(
                "GET",
                status_url,
                auth_token=config.get("token"),
            )
            if not success_detail:
                return
            status = str(detail.get("status", "")).upper() if isinstance(detail, dict) else ""
            if status in {"PASS", "FAIL"}:
                print("")
                print("Check Result:")
                self._print_check_result(detail)
                return
            time.sleep(3)
        print("")
        print(f"Check is still running. Use `icopa cloud check --id {parsed.id}` again to refresh.")

    @staticmethod
    def _print_check_result(check_payload: dict) -> None:
        check_data = check_payload.get("check_data") if isinstance(check_payload, dict) else {}
        if not isinstance(check_data, dict):
            check_data = {}
        vm_data = check_data.get("vm")
        if not isinstance(vm_data, dict):
            vm_data = {}

        print(f"check_request_id: {check_payload.get('id')}")
        print(f"status: {check_payload.get('status')}")
        print(f"provisioning_vm_status: {check_payload.get('provisioning_vm_status')}")
        print(f"task_id: {check_payload.get('task_id')}")
        print(f"updated_at: {check_payload.get('updated_at')}")
        if check_payload.get("last_error"):
            print(f"last_error: {check_payload.get('last_error')}")

        print(
            "scope:",
            f"subscription={check_data.get('subscription_id', '')}",
            f"resource_group={check_data.get('resource_group', '')}",
            f"vm_name={check_data.get('vm_name', '')}",
        )
        print(f"cloud_vm_exists: {check_data.get('cloud_vm_exists')}")
        if not vm_data:
            return

        rows = [
            ["VM_ID", vm_data.get("id", "")],
            ["LOCATION", vm_data.get("location", "")],
            ["SIZE", vm_data.get("vm_size", "")],
            ["PROVISIONING_STATE", vm_data.get("provisioning_state", "")],
            ["POWER_STATE", vm_data.get("power_state", "")],
            ["PUBLIC_IPS", ", ".join(vm_data.get("public_ips", []) or []) or "<none>"],
            ["PRIVATE_IPS", ", ".join(vm_data.get("private_ips", []) or []) or "<none>"],
            ["NICS", ", ".join(vm_data.get("network_interfaces", []) or []) or "<none>"],
            ["NSGS", ", ".join(vm_data.get("network_security_groups", []) or []) or "<none>"],
        ]
        print(tabulate(rows, headers=["FIELD", "VALUE"], tablefmt="github"))

    def list(self, *args):
        parser = self.commands["list"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        query = ""
        if str(parsed.provider or "").strip():
            provider = self._normalize_provider(parsed.provider)
            query = f"?provider={provider}"
        success, data = self.call_api(
            "GET",
            f"{config['server'].rstrip('/')}/{Endpoints.CLOUD_VMS}{query}",
            auth_token=config.get("token"),
        )
        if not success:
            return
        if isinstance(data, list) and data:
            rows = []
            for item in data:
                rows.append(
                    [
                        item.get("id"),
                        item.get("name"),
                        item.get("provider"),
                        item.get("status"),
                        (item.get("inventory_vm") or {}).get("name", ""),
                        item.get("task_id", ""),
                    ]
                )
            print(tabulate(rows, headers=["ID", "NAME", "PROVIDER", "STATUS", "INVENTORY_VM", "TASK_ID"], tablefmt="github"))
        else:
            print(data)

    def delete(self, *args):
        parser = self.commands["delete"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        success, data = self.call_api(
            "POST",
            f"{config['server'].rstrip('/')}/{Endpoints.CLOUD_VMS}{parsed.id}/delete/",
            json_data={},
            auth_token=config.get("token"),
        )
        if success:
            print(json.dumps(data, indent=2, sort_keys=True))

    def stop_vm(self, *args):
        parser = self.commands["stop_vm"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        success, data = self.call_api(
            "POST",
            f"{config['server'].rstrip('/')}/{Endpoints.CLOUD_VMS}{parsed.id}/stop/",
            json_data={},
            auth_token=config.get("token"),
        )
        if not success:
            return
        stop_request_id = int(data.get("stop_request_id", 0))
        print(
            "Stop dispatched:",
            f"provisioning_vm_id={data.get('provisioning_vm_id')}",
            f"stop_request_id={stop_request_id}",
            f"status={data.get('status')}",
            f"task_id={data.get('task_id')}",
        )
        if stop_request_id <= 0:
            return

        deadline = time.time() + max(parsed.wait_seconds, 1)
        while time.time() < deadline:
            status_url = (
                f"{config['server'].rstrip('/')}/"
                f"{Endpoints.CLOUD_VM_STOP_DETAIL.format(vm_id=parsed.id, stop_id=stop_request_id)}"
            )
            success_detail, detail = self.call_api(
                "GET",
                status_url,
                auth_token=config.get("token"),
            )
            if not success_detail:
                return
            status = str(detail.get("status", "")).upper() if isinstance(detail, dict) else ""
            if status in {"PASS", "FAIL"}:
                print("")
                print("Stop Result:")
                self._print_stop_result(detail)
                return
            time.sleep(3)
        print("")
        print(f"Stop is still running. Use `icopa cloud stop_vm --id {parsed.id}` again to refresh.")

    def start_vm(self, *args):
        parser = self.commands["start_vm"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        success, data = self.call_api(
            "POST",
            f"{config['server'].rstrip('/')}/{Endpoints.CLOUD_VMS}{parsed.id}/start/",
            json_data={},
            auth_token=config.get("token"),
        )
        if not success:
            return
        start_request_id = int(data.get("start_request_id", 0))
        print(
            "Start dispatched:",
            f"provisioning_vm_id={data.get('provisioning_vm_id')}",
            f"start_request_id={start_request_id}",
            f"status={data.get('status')}",
            f"task_id={data.get('task_id')}",
        )
        if start_request_id <= 0:
            return

        deadline = time.time() + max(parsed.wait_seconds, 1)
        while time.time() < deadline:
            status_url = (
                f"{config['server'].rstrip('/')}/"
                f"{Endpoints.CLOUD_VM_START_DETAIL.format(vm_id=parsed.id, start_id=start_request_id)}"
            )
            success_detail, detail = self.call_api(
                "GET",
                status_url,
                auth_token=config.get("token"),
            )
            if not success_detail:
                return
            status = str(detail.get("status", "")).upper() if isinstance(detail, dict) else ""
            if status in {"PASS", "FAIL"}:
                print("")
                print("Start Result:")
                self._print_start_result(detail)
                return
            time.sleep(3)
        print("")
        print(f"Start is still running. Use `icopa cloud start_vm --id {parsed.id}` again to refresh.")

    @staticmethod
    def _print_stop_result(stop_payload: dict) -> None:
        stop_data = stop_payload.get("stop_data") if isinstance(stop_payload, dict) else {}
        if not isinstance(stop_data, dict):
            stop_data = {}
        stop_result = stop_data.get("stop_result")
        if not isinstance(stop_result, dict):
            stop_result = {}
        lookup = stop_data.get("vm_lookup")
        if not isinstance(lookup, dict):
            lookup = {}
        vm_data = lookup.get("vm")
        if not isinstance(vm_data, dict):
            vm_data = {}

        print(f"stop_request_id: {stop_payload.get('id')}")
        print(f"status: {stop_payload.get('status')}")
        print(f"provisioning_vm_status: {stop_payload.get('provisioning_vm_status')}")
        print(f"task_id: {stop_payload.get('task_id')}")
        print(f"updated_at: {stop_payload.get('updated_at')}")
        if stop_payload.get("last_error"):
            print(f"last_error: {stop_payload.get('last_error')}")

        rows = [
            ["VM_NAME", stop_result.get("vm_name", "") or lookup.get("vm_name", "")],
            ["SUBSCRIPTION_ID", stop_result.get("subscription_id", "") or lookup.get("subscription_id", "")],
            ["RESOURCE_GROUP", stop_result.get("resource_group", "") or lookup.get("resource_group", "")],
            ["STATUS", stop_result.get("status", "")],
            ["POWER_STATE", stop_result.get("power_state", "") or vm_data.get("power_state", "")],
            ["LOCATION", vm_data.get("location", "") or (stop_result.get("details") or {}).get("location", "")],
            ["SIZE", vm_data.get("vm_size", "") or (stop_result.get("details") or {}).get("vm_size", "")],
            ["PUBLIC_IPS", ", ".join(vm_data.get("public_ips", []) or []) or "<none>"],
            ["NICS", ", ".join(vm_data.get("network_interfaces", []) or []) or "<none>"],
        ]
        print(tabulate(rows, headers=["FIELD", "VALUE"], tablefmt="github"))

    @staticmethod
    def _print_start_result(start_payload: dict) -> None:
        start_data = start_payload.get("start_data") if isinstance(start_payload, dict) else {}
        if not isinstance(start_data, dict):
            start_data = {}
        start_result = start_data.get("start_result")
        if not isinstance(start_result, dict):
            start_result = {}
        lookup = start_data.get("vm_lookup")
        if not isinstance(lookup, dict):
            lookup = {}
        vm_data = lookup.get("vm")
        if not isinstance(vm_data, dict):
            vm_data = {}

        print(f"start_request_id: {start_payload.get('id')}")
        print(f"status: {start_payload.get('status')}")
        print(f"provisioning_vm_status: {start_payload.get('provisioning_vm_status')}")
        print(f"task_id: {start_payload.get('task_id')}")
        print(f"updated_at: {start_payload.get('updated_at')}")
        if start_payload.get("last_error"):
            print(f"last_error: {start_payload.get('last_error')}")

        rows = [
            ["VM_NAME", start_result.get("vm_name", "") or lookup.get("vm_name", "")],
            ["SUBSCRIPTION_ID", start_result.get("subscription_id", "") or lookup.get("subscription_id", "")],
            ["RESOURCE_GROUP", start_result.get("resource_group", "") or lookup.get("resource_group", "")],
            ["STATUS", start_result.get("status", "")],
            ["POWER_STATE", start_result.get("power_state", "") or vm_data.get("power_state", "")],
            ["LOCATION", vm_data.get("location", "") or (start_result.get("details") or {}).get("location", "")],
            ["SIZE", vm_data.get("vm_size", "") or (start_result.get("details") or {}).get("vm_size", "")],
            ["PUBLIC_IPS", ", ".join(vm_data.get("public_ips", []) or []) or "<none>"],
            ["NICS", ", ".join(vm_data.get("network_interfaces", []) or []) or "<none>"],
        ]
        print(tabulate(rows, headers=["FIELD", "VALUE"], tablefmt="github"))

    def print_help(self):
        print(CLOUD_HELP)
