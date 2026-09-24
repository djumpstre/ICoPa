"""Command group for runtime environment operations."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from tabulate import tabulate

from icopa_cli.command_group.base import CommandGroupBase
from icopa_cli.endpoints import Endpoints
from icopa_cli.icopa_config import IcopaConfig


RUNTIME_HELP = """
ICoPa CLI [runtime] command group for runtime environment management

Usage:
    icopa runtime <command> [-args]

Commands:
    list                List runtime environment profiles
    upload              Upload runtime environment from a YAML file
    info                Show detailed information for one runtime environment
    actions             Show canonical action catalog and available presets
    delete              Delete one runtime environment

Resource:
    <runtime_env_name>
"""


class RuntimeCommandGroup(CommandGroupBase):
    """Command group [runtime]."""

    COMMAND_LIST = [
        "list",
        "upload",
        "info",
        "actions",
        "delete",
    ]

    def __init__(self, subparsers) -> None:
        super().__init__(subparsers, "runtime")
        self.init_subcommand_upload()
        self.init_subcommand_info()
        self.init_subcommand_actions()
        self.init_subcommand_delete()

    def init_subcommand_upload(self):
        parser = self.commands["upload"]
        parser.add_argument("--file", required=True, help="Path to runtime environment YAML file")
        parser.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            help="Print full values, including full multi-line command content.",
        )

    def init_subcommand_info(self):
        parser = self.commands["info"]
        parser.add_argument("resource", nargs="?", help="Runtime environment name, e.g. ros2-jazzy-zenoh")
        parser.add_argument("--id", dest="resource_id", type=int, help="Runtime environment id")

    def init_subcommand_delete(self):
        parser = self.commands["delete"]
        parser.add_argument("resource", help="Runtime environment name, e.g. ros2-jazzy-zenoh")

    def init_subcommand_actions(self):
        parser = self.commands["actions"]
        parser.add_argument("--env", dest="env_name", help="Runtime environment name to include runtime presets")

    def _get_context(self):
        return IcopaConfig.get_current_config()

    def _parse_env_resource(self, resource: str) -> str:
        # Backward compatible: accept either "<name>" or "env/<name>".
        env_name = resource.split("/", 1)[1].strip() if resource.startswith("env/") else resource.strip()
        if not env_name:
            raise ValueError("Runtime environment name is required.")
        return env_name

    @staticmethod
    def _resolve_env_name_from_id(resource_id: int, runtime_items: list) -> str:
        for item in runtime_items:
            if not isinstance(item, dict):
                continue
            if item.get("id") != resource_id:
                continue
            env_name = str(item.get("name") or "").strip()
            if env_name:
                return env_name
            break
        raise ValueError(f"Runtime environment id {resource_id} was not found.")

    def _print_runtime_info(self, runtime_env: dict):
        print(f"Name:          {runtime_env.get('name', '')}")
        print(f"Kind:          {runtime_env.get('kind', '')}")
        metadata = runtime_env.get("metadata") or {}
        print(f"Description:   {metadata.get('description', '')}")
        print(f"Created At:    {runtime_env.get('created_at', '')}")
        print(f"Updated At:    {runtime_env.get('updated_at', '')}")
        print(f"Upload Ver:    {runtime_env.get('uploaded_version', 0)}")
        print("")
        print("Metadata:")
        print(json.dumps(metadata, indent=2, sort_keys=True))
        print("")
        print("Images:")
        print(json.dumps(runtime_env.get("images") or {}, indent=2, sort_keys=True))
        print("")
        print("Tags:")
        print(json.dumps(runtime_env.get("tags") or {}, indent=2, sort_keys=True))
        print("")
        print("Parameters:")
        print(json.dumps(runtime_env.get("parameters") or {}, indent=2, sort_keys=True))
        print("")
        print(f"Runtime YAML:  {runtime_env.get('cached_yaml_file', '')}")
        print(f"RRT Config:    {runtime_env.get('serializer_rrt_config_file', '')}")
        print(f"RRT Path:      {runtime_env.get('serializer_rrt_config_path', '')}")
        print("")
        print("Command Presets:")
        presets = runtime_env.get("command_preset") or []
        if not presets:
            print("(none)")
            return

        for preset in presets:
            if not isinstance(preset, dict):
                print(str(preset))
                print("")
                continue
            if preset.get("group"):
                print(f"Group:           {preset.get('group')}")
            print(f"Preset Name:     {preset.get('name', '')}")
            print(f"Executor:        {preset.get('executor', '')}")
            print("Command:")
            command = preset.get("command") or ""
            print(command.rstrip("\n"))
            print("")

        groups = runtime_env.get("command_groups") or {}
        if isinstance(groups, dict) and groups:
            print("Command Groups:")
            print(json.dumps(groups, indent=2, sort_keys=True))

    def list(self):
        config = self._get_context()
        success, data = self.call_api(
            "GET",
            f"{config['server'].rstrip('/')}/{Endpoints.RUNTIME_ENV}",
            auth_token=config.get("token"),
        )
        if success:
            if isinstance(data, list) and data:
                rows = []
                for item in data:
                    rows.append(
                        [
                            item.get("id"),
                            item.get("name"),
                            item.get("kind"),
                            len(item.get("command_preset") or []),
                            (item.get("images") or {}).get("runnerImage", ""),
                        ]
                    )
                print(
                    tabulate(
                        rows,
                        headers=["ID", "NAME", "KIND", "COMMAND_PRESETS", "RUNNER_IMAGE"],
                        tablefmt="github",
                    )
                )
            else:
                print(data)

    def upload(self, *args):
        parser = self.commands["upload"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        runtime_file = Path(parsed.file).expanduser().resolve()
        if not runtime_file.exists():
            parser.error(f"Runtime YAML file not found: {runtime_file}")

        rrt_upload_path = self._resolve_rrt_config_file_path(runtime_file)
        with runtime_file.open("rb") as handle:
            files = {"file": (str(runtime_file), handle, "application/x-yaml")}
            rrt_handle = None
            if rrt_upload_path is not None:
                rrt_handle = rrt_upload_path.open("rb")
                files["rrt_config_file"] = (
                    str(rrt_upload_path),
                    rrt_handle,
                    "application/x-yaml",
                )
            success, data = self.call_api(
                "POST",
                f"{config['server'].rstrip('/')}/{Endpoints.RUNTIME_ENV}",
                files=files,
                auth_token=config.get("token"),
            )
            if rrt_handle is not None:
                rrt_handle.close()
        if success:
            self._print_upload_summary(data, verbose=parsed.verbose)

    def _print_upload_summary(self, data: dict, *, verbose: bool) -> None:
        if not isinstance(data, dict):
            print(data)
            return
        print(data.get("message", "Runtime environment upload request finished."))

        created = data.get("created") if isinstance(data.get("created"), dict) else {}
        updated = data.get("updated") if isinstance(data.get("updated"), dict) else {}
        created_envs = created.get("runtime_env") if isinstance(created, dict) else []
        updated_envs = updated.get("runtime_env") if isinstance(updated, dict) else []

        if isinstance(created_envs, list):
            for item in created_envs:
                if not isinstance(item, dict):
                    continue
                print(f"Created Runtime Env: {item.get('name', '-')}")
                print(f"Kind:                {item.get('kind', '-')}")

        if isinstance(updated_envs, list) and updated_envs:
            self._print_updated_fields(updated_envs, entity_label="Runtime Env", verbose=verbose)
        elif not created_envs:
            print("No changes detected.")

        artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), dict) else {}
        if artifacts:
            print(f"Upload Version:      {artifacts.get('uploaded_version', '-')}")
            print(f"Runtime YAML Path:   {artifacts.get('runtime_yaml_path', '-')}")
            print(f"RRT Config Path:     {artifacts.get('rrt_config_yaml_path', '-')}")
            print(f"RRT Config Source:   {artifacts.get('rrt_config_source_file', '-')}")

        total = data.get("total") if isinstance(data.get("total"), dict) else {}
        if isinstance(total, dict):
            print(f"Total Runtime Envs:  {total.get('runtime_env', '-')}")

    @staticmethod
    def _resolve_rrt_config_file_path(runtime_yaml_path: Path) -> Path | None:
        try:
            payload = yaml.safe_load(runtime_yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        body = payload.get("spec") if isinstance(payload.get("spec"), dict) else payload
        params = body.get("parameters") if isinstance(body, dict) else {}
        if not isinstance(params, dict):
            return None
        raw_ref = str(
            params.get("rrt_config_file_path")
            or params.get("rrtConfigFilePath")
            or ""
        ).strip()
        if not raw_ref:
            return None
        candidate = Path(raw_ref).expanduser()
        if candidate.is_absolute() and candidate.exists() and candidate.is_file():
            return candidate
        relative_candidates = [
            runtime_yaml_path.parent / raw_ref,
            Path.cwd() / raw_ref,
        ]
        for item in relative_candidates:
            resolved = item.resolve()
            if resolved.exists() and resolved.is_file():
                return resolved
        return None

    def info(self, *args):
        parser = self.commands["info"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        if parsed.resource and parsed.resource_id is not None:
            parser.error("Provide either <runtime_env_name> or --id, not both.")

        if parsed.resource_id is not None:
            success, runtime_items = self.call_api(
                "GET",
                f"{config['server'].rstrip('/')}/{Endpoints.RUNTIME_ENV}",
                auth_token=config.get("token"),
            )
            if not success or not isinstance(runtime_items, list):
                parser.error("Unable to resolve runtime environment name from id.")
            try:
                env_name = self._resolve_env_name_from_id(parsed.resource_id, runtime_items)
            except ValueError as exc:
                parser.error(str(exc))
        else:
            if not parsed.resource:
                parser.error("Either <runtime_env_name> or --id is required.")
            try:
                env_name = self._parse_env_resource(parsed.resource)
            except ValueError as exc:
                parser.error(str(exc))
        success, data = self.call_api(
            "GET",
            f"{config['server'].rstrip('/')}/{Endpoints.RUNTIME_ENV_DETAIL}{env_name}/",
            auth_token=config.get("token"),
        )
        if success:
            self._print_runtime_info(data)

    def delete(self, *args):
        parser = self.commands["delete"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        try:
            env_name = self._parse_env_resource(parsed.resource)
        except ValueError as exc:
            parser.error(str(exc))
        success, data = self.call_api(
            "DELETE",
            f"{config['server'].rstrip('/')}/{Endpoints.RUNTIME_ENV_DETAIL}{env_name}/",
            auth_token=config.get("token"),
        )
        if success:
            print(data)

    def actions(self, *args):
        parser = self.commands["actions"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        base_url = f"{config['server'].rstrip('/')}/{Endpoints.RUNTIME_ENV_ACTIONS}"
        if parsed.env_name:
            base_url = f"{base_url}?env_name={parsed.env_name}"
        success, data = self.call_api(
            "GET",
            base_url,
            auth_token=config.get("token"),
        )
        if not success or not isinstance(data, dict):
            return

        canonical_actions = data.get("canonical_actions") if isinstance(data.get("canonical_actions"), list) else []
        runtime_presets = data.get("runtime_presets") if isinstance(data.get("runtime_presets"), list) else []
        builtin_presets = data.get("builtin_presets") if isinstance(data.get("builtin_presets"), dict) else {}

        print("Canonical Actions:")
        if canonical_actions:
            rows = []
            for item in canonical_actions:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    [
                        str(item.get("type") or ""),
                        str(item.get("scope") or ""),
                        str(item.get("target_mode") or ""),
                        ", ".join(str(x) for x in item.get("required_parameters") or []),
                        str(item.get("description") or ""),
                    ]
                )
            print(
                tabulate(
                    rows,
                    headers=["TYPE", "SCOPE", "TARGET_MODE", "REQUIRED_PARAMS", "DESCRIPTION"],
                    tablefmt="github",
                )
            )
        else:
            print("(none)")

        print("")
        runtime_env_name = str(data.get("runtime_env") or "").strip()
        print(f"Runtime Presets ({runtime_env_name or 'none selected'}):")
        if runtime_presets:
            rows = []
            for item in runtime_presets:
                if not isinstance(item, dict):
                    continue
                rows.append([str(item.get("group") or ""), str(item.get("name") or ""), str(item.get("executor") or "")])
            print(tabulate(rows, headers=["GROUP", "NAME", "EXECUTOR"], tablefmt="github"))
        else:
            print("(none)")

        print("")
        print("Built-in Presets:")
        for section in ("stress", "routing"):
            section_items = builtin_presets.get(section) if isinstance(builtin_presets, dict) else []
            print(f"- {section}")
            if isinstance(section_items, list) and section_items:
                rows = []
                for item in section_items:
                    if isinstance(item, dict):
                        rows.append([str(item.get("name") or ""), str(item.get("description") or "")])
                print(tabulate(rows, headers=["NAME", "DESCRIPTION"], tablefmt="github"))
            else:
                print("(none)")
            print("")

    def print_help(self):
        print(RUNTIME_HELP)
