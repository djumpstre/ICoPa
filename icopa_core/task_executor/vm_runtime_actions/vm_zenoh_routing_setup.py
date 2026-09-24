"""Routing setup executors for runtime-tag-specific VM workflows."""

from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import re
from typing import Any

from ..base_exec import ActionExecutionError, ActionExecutionResult
from ..vm_exec import VMProfilingBaseExec
import yaml

_DEFAULT_ZENOH_ROUTER_CONTAINER_NAME = "icopa-zenoh-router"
_LEGACY_ZENOH_ROUTER_CONTAINER_NAME = "zenoh-router"
_DEFAULT_ZENOH_ROUTER_PORT = 7447
_DEFAULT_CLOUD_NODE_NAME = "cloud_vm_bw"
_DOCKER_NAME_PATTERN = re.compile(r"--name\s+\S+")
_ENDPOINT_PORT_PATTERN = re.compile(r":(\d+)$")
_ROUTER_PRESET_PATH = Path(__file__).resolve().parent / "config" / "preset_zenoh_router_containers.yaml"
_ROUTING_SETUP_PRESET_PREFIX = "routing/setup_zenoh_routing"
_ROUTER_PRESET_ID_PREFIX = "zenoh_router:"
_DEFAULT_ROUTER_EXEC_CMD_TYPE = "vm_ce_router_exec_cmd"
_DEFAULT_ROUTING_COMMAND_TIMEOUT_SEC = 600
_LEGACY_ROUTING_PRESET_TO_CMD_TYPE = {
    "routing/cloud_router": "vm_ce_router_exec_cmd",
    "routing/local_router": "vm_local_router_exec_cmd",
}


def _builtin_zenoh_routing_data() -> dict[str, Any]:
    path = Path(os.environ.get("ICOPA_ZENOH_ROUTER_PRESETS_PATH", str(_ROUTER_PRESET_PATH))).expanduser()
    return _load_zenoh_routing_data(path)


@lru_cache(maxsize=1)
def _load_zenoh_routing_data(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ActionExecutionError(
            f"Zenoh router preset file was not found: {path}"
        )
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    presets_raw = raw.get("presets")
    if not isinstance(presets_raw, (list, dict)):
        raise ActionExecutionError(
            f"Zenoh router preset file is invalid: {path} (missing 'presets' list or mapping)."
        )

    presets_by_id: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    legacy_by_name: dict[str, str] = {}

    if isinstance(presets_raw, dict):
        for preset_name, payload in presets_raw.items():
            if not isinstance(payload, dict):
                raise ActionExecutionError(
                    f"Zenoh router preset '{preset_name}' must be an object in {path}."
                )
            command = str(payload.get("command") or "").strip()
            if not command:
                raise ActionExecutionError(
                    f"Zenoh router preset '{preset_name}' has empty command in {path}."
                )
            legacy_by_name[_normalize_preset_name(str(preset_name))] = command
    else:
        for idx, payload in enumerate(presets_raw):
            if not isinstance(payload, dict):
                raise ActionExecutionError(
                    f"Zenoh router preset entry presets[{idx}] must be an object in {path}."
                )
            preset_id = str(payload.get("id") or "").strip()
            if not preset_id:
                raise ActionExecutionError(
                    f"Built-in Zenoh router preset entry presets[{idx}] requires non-empty field 'id'."
                )
            commands_raw = payload.get("commands")
            if not isinstance(commands_raw, list) or not commands_raw:
                raise ActionExecutionError(
                    f"Built-in Zenoh router preset '{preset_id}' requires non-empty list field 'commands'."
                )
            commands: dict[str, str] = {}
            for cmd_index, cmd_item in enumerate(commands_raw):
                if not isinstance(cmd_item, dict):
                    raise ActionExecutionError(
                        f"Built-in Zenoh router preset '{preset_id}' commands[{cmd_index}] must be an object."
                    )
                cmd_type = str(cmd_item.get("type") or "").strip()
                command = str(cmd_item.get("command") or "").strip()
                if not cmd_type or not command:
                    raise ActionExecutionError(
                        f"Built-in Zenoh router preset '{preset_id}' commands[{cmd_index}] requires non-empty "
                        f"'type' and 'command'."
                    )
                commands[cmd_type] = command

            parameters = payload.get("parameters")
            if parameters in (None, ""):
                parameters = {}
            if not isinstance(parameters, dict):
                raise ActionExecutionError(
                    f"Built-in Zenoh router preset '{preset_id}' field 'parameters' must be an object."
                )
            presets_by_id[preset_id] = {
                "id": preset_id,
                "name": str(payload.get("name") or preset_id),
                "description": str(payload.get("description") or ""),
                "image": str(payload.get("image") or "").strip(),
                "parameters": parameters,
                "commands": commands,
            }
            ordered_ids.append(preset_id)

    return {
        "presets_by_id": presets_by_id,
        "ordered_ids": ordered_ids,
        "legacy_by_name": legacy_by_name,
    }


def builtin_zenoh_routing_preset_names() -> set[str]:
    data = _builtin_zenoh_routing_data()
    names = set(data.get("legacy_by_name", {}).keys())
    names.update(_LEGACY_ROUTING_PRESET_TO_CMD_TYPE.keys())
    names.add(_ROUTING_SETUP_PRESET_PREFIX)
    for preset_id in data.get("ordered_ids", []):
        preset_suffix = str(preset_id)
        if ":" in preset_suffix:
            preset_suffix = preset_suffix.split(":", 1)[1]
        names.add(f"{_ROUTING_SETUP_PRESET_PREFIX}/{preset_suffix}")
    names.add("routing/clean_router")
    return names


def _normalize_preset_name(raw_preset: str) -> str:
    if "/" in raw_preset:
        return raw_preset
    return f"routing/{raw_preset}"


def _extract_endpoint_port(raw_endpoint: Any) -> int | None:
    if raw_endpoint in (None, ""):
        return None
    text = str(raw_endpoint).strip()
    match = _ENDPOINT_PORT_PATTERN.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _extract_open_ports_from_vm(vm: dict[str, Any]) -> set[int]:
    networking = vm.get("networking")
    if not isinstance(networking, dict):
        return set()
    open_ports: set[int] = set()
    for key in ("open_ports", "openPorts"):
        raw = networking.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, int):
                open_ports.add(item)
            elif isinstance(item, str) and item.isdigit():
                open_ports.add(int(item))
            elif isinstance(item, dict):
                port_value = item.get("port")
                if isinstance(port_value, int):
                    open_ports.add(port_value)
                elif isinstance(port_value, str) and port_value.isdigit():
                    open_ports.add(int(port_value))
    for key in ("allow_inbound", "allowed_inbound", "inbound", "ingress"):
        raw = networking.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            port_value = item.get("port")
            if isinstance(port_value, int):
                open_ports.add(port_value)
            elif isinstance(port_value, str) and port_value.isdigit():
                open_ports.add(int(port_value))
    return open_ports


def _extract_zenoh_port_from_vm(vm: dict[str, Any]) -> int | None:
    networking = vm.get("networking")
    if not isinstance(networking, dict):
        return None
    for key in ("open_ports", "openPorts", "allow_inbound", "allowed_inbound", "inbound", "ingress"):
        raw = networking.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            purpose = str(item.get("purpose") or "").lower()
            proto = str(item.get("proto") or "").lower()
            port_value = item.get("port")
            if "zenoh" not in purpose:
                continue
            if proto and proto != "tcp":
                continue
            if isinstance(port_value, int):
                return port_value
            if isinstance(port_value, str) and port_value.isdigit():
                return int(port_value)
    return None


class VMProfilingRoutingSetupExec(VMProfilingBaseExec):
    """Generic routing setup executor specialization."""

    action_type = "run_runtime_preset:routing"

    def validate_input(self) -> None:
        super().validate_input()
        preset = _normalize_preset_name(str(self.ctx.require_action_field("preset")))
        if not preset.startswith("routing/"):
            raise ActionExecutionError("VMProfilingRoutingSetupExec expects preset in 'routing/*'.")


class VMZenohRoutingSetupExec(VMProfilingRoutingSetupExec):
    """Zenoh-specific routing setup with fixed container naming and clean support."""

    action_type = "run_runtime_preset:routing:zenoh"

    def _resolve_router_container_name(self) -> str:
        action_overrides = self.ctx.action.get("parameters")
        if isinstance(action_overrides, dict):
            value = action_overrides.get("router_container_name")
            if value:
                return str(value)
        runtime_parameters = self.ctx.runtime_env.get("parameters")
        if isinstance(runtime_parameters, dict):
            value = runtime_parameters.get("router_container_name") or runtime_parameters.get(
                "zenoh_router_container_name"
            )
            if value:
                return str(value)
        return _DEFAULT_ZENOH_ROUTER_CONTAINER_NAME

    def _resolve_cloud_node_name(self, target_node: str, *, exec_cmd_type: str = "") -> str:
        action_params = self._action_parameters()
        explicit_peer = str(
            action_params.get("peer_node")
            or action_params.get("peerNode")
            or self.ctx.action.get("peer_node")
            or self.ctx.action.get("peerNode")
            or ""
        ).strip()
        if explicit_peer:
            if explicit_peer not in self.ctx.inventory_by_node:
                raise ActionExecutionError(
                    f"peer_node '{explicit_peer}' was not found in inventory_by_node."
                )
            return explicit_peer
        if exec_cmd_type == "vm_ce_router_exec_cmd" and target_node in self.ctx.inventory_by_node:
            return target_node
        if _DEFAULT_CLOUD_NODE_NAME in self.ctx.inventory_by_node:
            return _DEFAULT_CLOUD_NODE_NAME
        if target_node != _DEFAULT_CLOUD_NODE_NAME and target_node in self.ctx.inventory_by_node:
            return target_node
        for node_name in self.ctx.inventory_by_node:
            if node_name != target_node:
                return node_name
        return target_node

    def _resolve_cloud_vm(self, target_node: str, *, exec_cmd_type: str = "") -> dict[str, Any]:
        cloud_node_name = self._resolve_cloud_node_name(target_node, exec_cmd_type=exec_cmd_type)
        return self.ctx.inventory_by_node.get(cloud_node_name, self.ctx.inventory_by_node.get(target_node, {}))

    def _resolve_zenoh_router_port(self, cloud_vm: dict[str, Any]) -> int:
        from_inventory = _extract_zenoh_port_from_vm(cloud_vm)
        if from_inventory is not None:
            return from_inventory
        tags = self.ctx.runtime_env.get("tags")
        if isinstance(tags, dict):
            zenoh = tags.get("zenoh")
            if isinstance(zenoh, dict):
                raw_port = zenoh.get("routerPort") or zenoh.get("router_port")
                if isinstance(raw_port, int):
                    return raw_port
                if isinstance(raw_port, str) and raw_port.isdigit():
                    return int(raw_port)
        runtime_parameters = self.ctx.runtime_env.get("parameters")
        if isinstance(runtime_parameters, dict):
            from_endpoint = _extract_endpoint_port(runtime_parameters.get("cloud_router_ep"))
            if from_endpoint is not None:
                return from_endpoint
        return _DEFAULT_ZENOH_ROUTER_PORT

    def _resolve_cloud_router_endpoint(self, cloud_vm: dict[str, Any]) -> str:
        host = str(cloud_vm.get("address") or "")
        if not host:
            return ""
        return f"tcp/{host}:{self._resolve_zenoh_router_port(cloud_vm)}"

    @staticmethod
    def _normalize_endpoint_list(raw: Any) -> list[str]:
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return [raw.strip()]
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        return []

    def _resolve_exec_cmd_type_hint(self) -> str:
        action_params = self._action_parameters()
        explicit = str(action_params.get("exec_cmd_type") or "").strip()
        if explicit:
            return explicit
        requested = _normalize_preset_name(str(self.ctx.action.get("preset") or ""))
        if requested in _LEGACY_ROUTING_PRESET_TO_CMD_TYPE:
            return _LEGACY_ROUTING_PRESET_TO_CMD_TYPE[requested]
        return _DEFAULT_ROUTER_EXEC_CMD_TYPE

    def _build_template_values(self) -> dict[str, Any]:
        values = super()._build_template_values()
        target_node = str(self.ctx.action.get("target") or "")
        exec_cmd_type = self._resolve_exec_cmd_type_hint()
        cloud_vm = self._resolve_cloud_vm(target_node, exec_cmd_type=exec_cmd_type)
        endpoint = self._resolve_cloud_router_endpoint(cloud_vm)
        if endpoint:
            values["cloud_router_ep"] = endpoint
            values["cloudRouterEp"] = endpoint
        required_port = self._resolve_zenoh_router_port(cloud_vm)
        required_listen_ep = f"tcp/0.0.0.0:{required_port}"
        action_params = self._action_parameters()
        action_listen_override = self._normalize_endpoint_list(
            action_params.get("cloud_router_listen_ep")
            if action_params.get("cloud_router_listen_ep") not in (None, "")
            else action_params.get("cloudRouterListenEp")
        )
        listen_endpoints = self._normalize_endpoint_list(
            values.get("cloud_router_listen_ep")
            if values.get("cloud_router_listen_ep") not in (None, "")
            else values.get("cloudRouterListenEp")
        )
        if exec_cmd_type == "vm_ce_router_exec_cmd":
            # For cloud/edge router setup, inventory-selected zenoh port is authoritative by default.
            default_listen_ep = f"tcp/0.0.0.0:{_DEFAULT_ZENOH_ROUTER_PORT}"
            required_defaults = [default_listen_ep]
            if required_listen_ep != default_listen_ep:
                required_defaults.append(required_listen_ep)
            if action_listen_override:
                merged_endpoints = [*required_defaults, *action_listen_override]
                listen_endpoints = list(dict.fromkeys(merged_endpoints))
            else:
                listen_endpoints = required_defaults
        else:
            if not listen_endpoints:
                listen_endpoints = [required_listen_ep]
            elif required_listen_ep not in listen_endpoints:
                listen_endpoints.append(required_listen_ep)
        listen_endpoints_json = json.dumps(listen_endpoints)
        values["cloud_router_listen_ep"] = listen_endpoints_json
        values["cloudRouterListenEp"] = listen_endpoints_json
        return values

    def _action_parameters(self) -> dict[str, Any]:
        params = self.ctx.action.get("parameters")
        if isinstance(params, dict):
            return params
        return {}

    @staticmethod
    def _has_non_empty_runner_image(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False
        for key in ("runner_image", "runnerImage"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return True
        return False

    def _runner_image_overridden_by_runtime_or_action(self) -> bool:
        action_params = self._action_parameters()
        runtime_images = self.ctx.runtime_env.get("images")
        runtime_parameters = self.ctx.runtime_env.get("parameters")
        return any(
            self._has_non_empty_runner_image(item)
            for item in (action_params, runtime_images, runtime_parameters)
        )

    @staticmethod
    def _preset_version_from_name(requested: str) -> str:
        if not requested.startswith(_ROUTING_SETUP_PRESET_PREFIX):
            return ""
        return requested[len(_ROUTING_SETUP_PRESET_PREFIX) :].lstrip("/")

    @staticmethod
    def _as_command_template_value(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return value

    def _resolve_builtin_router_preset_id(self, *, requested: str, data: dict[str, Any]) -> str:
        presets_by_id = data.get("presets_by_id")
        ordered_ids = data.get("ordered_ids")
        if not isinstance(presets_by_id, dict) or not isinstance(ordered_ids, list):
            raise ActionExecutionError("Built-in zenoh router preset catalog is invalid.")
        if not ordered_ids:
            raise ActionExecutionError(
                f"Built-in zenoh router preset catalog has no entries in {_ROUTER_PRESET_PATH}."
            )

        action_params = self._action_parameters()
        requested_id = str(action_params.get("preset_container_id") or "").strip()
        if requested_id:
            if requested_id in presets_by_id:
                return requested_id
            available_ids = ", ".join(sorted(str(item) for item in presets_by_id.keys()))
            raise ActionExecutionError(
                f"Unknown preset_container_id '{requested_id}'. Available ids: {available_ids}."
            )

        version = self._preset_version_from_name(requested)
        if version:
            version_candidates = [version]
            if ":" not in version:
                version_candidates.append(f"{_ROUTER_PRESET_ID_PREFIX}{version}")
            for candidate in version_candidates:
                if candidate in presets_by_id:
                    return candidate
            available_ids = ", ".join(sorted(str(item) for item in presets_by_id.keys()))
            raise ActionExecutionError(
                f"Unknown routing preset version '{version}'. Available preset ids: {available_ids}."
            )

        return str(ordered_ids[0])

    def _resolve_exec_cmd_type(self, *, requested: str) -> str:
        action_params = self._action_parameters()
        explicit = str(action_params.get("exec_cmd_type") or "").strip()
        if explicit:
            return explicit
        if requested in _LEGACY_ROUTING_PRESET_TO_CMD_TYPE:
            return _LEGACY_ROUTING_PRESET_TO_CMD_TYPE[requested]
        return _DEFAULT_ROUTER_EXEC_CMD_TYPE

    def _resolve_runtime_preset(self) -> dict[str, Any]:
        requested = _normalize_preset_name(str(self.ctx.require_action_field("preset")))
        self.ctx.action["preset"] = requested
        if requested == "routing/clean_router":
            return {
                "group": "routing",
                "name": "clean_router",
                "executor": "ssh",
                "command": "",
            }

        data = _builtin_zenoh_routing_data()
        legacy_map = data.get("legacy_by_name")
        if isinstance(legacy_map, dict):
            legacy_command = legacy_map.get(requested)
            if legacy_command:
                return {
                    "group": "routing",
                    "name": requested.split("/", 1)[1],
                    "executor": "ssh",
                    "command": legacy_command,
                    "source": "builtin_zenoh_router_containers",
                }

        if requested.startswith(_ROUTING_SETUP_PRESET_PREFIX) or requested in _LEGACY_ROUTING_PRESET_TO_CMD_TYPE:
            preset_id = self._resolve_builtin_router_preset_id(requested=requested, data=data)
            presets_by_id = data.get("presets_by_id") if isinstance(data.get("presets_by_id"), dict) else {}
            preset_payload = presets_by_id.get(preset_id)
            if not isinstance(preset_payload, dict):
                raise ActionExecutionError(f"Router preset id '{preset_id}' was not found in built-in catalog.")

            exec_cmd_type = self._resolve_exec_cmd_type(requested=requested)
            commands = preset_payload.get("commands")
            command_template = commands.get(exec_cmd_type) if isinstance(commands, dict) else None
            if not command_template:
                available_cmd_types = ", ".join(
                    sorted(str(item) for item in (commands or {}).keys())
                )
                raise ActionExecutionError(
                    f"Router preset '{preset_id}' does not define command type '{exec_cmd_type}'. "
                    f"Available command types: {available_cmd_types}."
                )

            preset_parameters = dict(preset_payload.get("parameters") or {})
            image = str(preset_payload.get("image") or "").strip()
            if self._runner_image_overridden_by_runtime_or_action():
                preset_parameters.pop("runner_image", None)
                preset_parameters.pop("runnerImage", None)
            elif image:
                preset_parameters.setdefault("runner_image", image)
            normalized_parameters = {
                str(key): self._as_command_template_value(value)
                for key, value in preset_parameters.items()
            }
            return {
                "group": "routing",
                "name": str(preset_payload.get("name") or preset_id),
                "executor": "ssh",
                "command": str(command_template),
                "parameters": normalized_parameters,
                "preset_container_id": preset_id,
                "exec_cmd_type": exec_cmd_type,
                "source": "builtin_zenoh_router_containers",
            }

        allowed = ", ".join(sorted(builtin_zenoh_routing_preset_names()))
        raise ActionExecutionError(
            f"Unsupported zenoh routing preset '{requested}'. Allowed presets: {allowed}."
        )

    def _render_zenoh_command(self, preset_name: str, command_template: str) -> tuple[str, str]:
        container_name = self._resolve_router_container_name()
        cleanup_command = (
            f"docker rm -f {container_name} {_LEGACY_ZENOH_ROUTER_CONTAINER_NAME} >/dev/null 2>&1 || true"
        )
        if preset_name == "routing/clean_router":
            return cleanup_command, cleanup_command

        rendered_command = self._render_command(command_template)
        if "--name" in rendered_command:
            rendered_command = _DOCKER_NAME_PATTERN.sub(f"--name {container_name}", rendered_command, count=1)
        else:
            rendered_command = rendered_command.replace(
                "docker run -d ",
                f"docker run -d --name {container_name} ",
                1,
            )
        return rendered_command, rendered_command

    @staticmethod
    def _running_status_command(container_name: str) -> str:
        return (
            f"docker ps --filter \"name=^/{container_name}$\" "
            f"--format '{{{{.Names}}}}|{{{{.Status}}}}'"
        )

    @staticmethod
    def _all_status_command(container_name: str) -> str:
        return (
            f"docker ps -a --filter \"name=^/{container_name}$\" "
            f"--format '{{{{.Names}}}}|{{{{.Status}}}}'"
        )

    def _validate_cloud_router_port(
        self,
        *,
        cloud_vm: dict[str, Any],
        preset_name: str,
        exec_cmd_type: str,
    ) -> dict[str, Any]:
        required_port = self._resolve_zenoh_router_port(cloud_vm)
        open_ports = sorted(_extract_open_ports_from_vm(cloud_vm))
        warning = ""
        is_cloud_router = preset_name == "routing/cloud_router" or exec_cmd_type == "vm_ce_router_exec_cmd"
        if is_cloud_router and open_ports and required_port not in open_ports:
            warning = (
                f"Inventory open ports do not include computed zenoh port {required_port}; "
                f"continuing with command execution. open_ports={open_ports}"
            )
            strict_port_check = bool(self.ctx.action.get("strict_port_check", False))
            if strict_port_check:
                raise ActionExecutionError(warning)
        return {"required_port": required_port, "open_ports": open_ports, "warning": warning}

    def execute_impl(self) -> ActionExecutionResult:
        vm = self.ctx.get_target_vm()
        preset = self._resolve_runtime_preset()
        preset_parameters = preset.get("parameters")
        if isinstance(preset_parameters, dict):
            self.ctx.action["__preset_parameters"] = preset_parameters
        target = str(self.ctx.action.get("target"))
        preset_name = _normalize_preset_name(str(self.ctx.require_action_field("preset")))
        command_template = str(preset.get("command") or "")
        rendered_command, final_command = self._render_zenoh_command(preset_name, command_template)
        timeout_sec = int(self.ctx.action.get("timeout_sec") or _DEFAULT_ROUTING_COMMAND_TIMEOUT_SEC)
        exec_cmd_type = str(preset.get("exec_cmd_type") or "")
        cloud_node_name = self._resolve_cloud_node_name(target, exec_cmd_type=exec_cmd_type)
        cloud_vm = self._resolve_cloud_vm(target, exec_cmd_type=exec_cmd_type)
        port_validation = self._validate_cloud_router_port(
            cloud_vm=cloud_vm,
            preset_name=preset_name,
            exec_cmd_type=exec_cmd_type,
        )

        ssh_client = self._build_ssh_client(vm)
        command_result = ssh_client.execute_command(final_command, timeout=timeout_sec)
        container_name = self._resolve_router_container_name()
        running_status_cmd = self._running_status_command(container_name)
        all_status_cmd = self._all_status_command(container_name)
        container_running = False
        container_status_running = ""
        container_status_all = ""
        container_logs_tail = ""

        is_clean_router = preset_name == "routing/clean_router"
        success = bool(command_result.success)
        if success and not is_clean_router:
            running_result = ssh_client.execute_command(running_status_cmd, timeout=15)
            container_status_running = running_result.stdout or running_result.stderr or ""
            container_running = (
                running_result.success
                and container_name in (running_result.stdout or "")
            )
            success = container_running
            if not success:
                status_all_result = ssh_client.execute_command(all_status_cmd, timeout=15)
                container_status_all = status_all_result.stdout or status_all_result.stderr or ""
                logs_result = ssh_client.execute_command(
                    f"docker logs --tail 80 {container_name}",
                    timeout=20,
                )
                container_logs_tail = logs_result.stdout or logs_result.stderr or ""
        status = "SUCCESS" if success else "FAILED"
        failure_hint = ""
        if not success:
            first_stderr_line = str(command_result.stderr or "").strip().splitlines()
            if first_stderr_line:
                failure_hint = f" reason={first_stderr_line[0]}"
        return ActionExecutionResult(
            action_type=self.action_type,
            target=target,
            success=success,
            status=status,
            message=(
                (
                    f"Zenoh routing preset '{preset.get('name')}' executed and "
                    f"container '{container_name}' is running."
                    if not is_clean_router
                    else f"Zenoh routing preset '{preset.get('name')}' executed."
                )
                if success
                else (
                    f"Zenoh routing preset '{preset.get('name')}' command succeeded, "
                    f"but container '{container_name}' is not running."
                    if command_result.success and not is_clean_router
                    else f"Zenoh routing preset '{preset.get('name')}' failed.{failure_hint}"
                )
            ),
            logs=[command_result.stdout] if command_result.stdout else [],
            debug={
                "preset": {
                    "group": preset.get("group", ""),
                    "name": preset.get("name", ""),
                    "executor": preset.get("executor", ""),
                    "requested": preset_name,
                    "preset_container_id": preset.get("preset_container_id", ""),
                    "exec_cmd_type": preset.get("exec_cmd_type", ""),
                },
                "container_name": container_name,
                "command_template": command_template,
                "command_rendered": rendered_command,
                "command_final": final_command,
                "vm": {
                    "host": vm.get("address", ""),
                    "user": vm.get("user_name", ""),
                    "port": vm.get("port", 22),
                },
                "required_router_port": port_validation["required_port"],
                "open_ports": port_validation["open_ports"],
                "port_check_warning": port_validation["warning"],
                "cloud_router_node": cloud_node_name,
                "computed_cloud_router_ep": self._resolve_cloud_router_endpoint(cloud_vm),
                "returncode": command_result.returncode,
                "stderr": command_result.stderr,
                "running_status_cmd": running_status_cmd if not is_clean_router else "",
                "container_running": container_running if not is_clean_router else False,
                "container_status_running": container_status_running,
                "container_status_all": container_status_all,
                "container_logs_tail": container_logs_tail,
                "managed_containers_removed": (
                    [container_name, _LEGACY_ZENOH_ROUTER_CONTAINER_NAME]
                    if is_clean_router
                    else []
                ),
                "managed_containers_created": ([container_name] if success and not is_clean_router else []),
                "managed_containers_running": ([container_name] if success and not is_clean_router else []),
            },
        )
