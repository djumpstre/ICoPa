"""Command group for scenario operations."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from tabulate import tabulate

from icopa_cli.command_group.base import CommandGroupBase
from icopa_cli.endpoints import Endpoints
from icopa_cli.icopa_config import IcopaConfig


SCENARIO_HELP = """
ICoPa CLI [scenario] command group for scenario management

Usage:
    icopa scenario <command> [-args]

Commands:
    list                List scenario profiles
    upload              Upload scenario from a YAML file
    info                Show detailed information for one scenario
    delete              Delete one scenario
    validate            Validate scenario nodes/graph/actions before experiments

Resource:
    <scenario_name>
"""


class ScenarioCommandGroup(CommandGroupBase):
    """Command group [scenario]."""

    COMMAND_LIST = [
        "list",
        "upload",
        "info",
        "delete",
        "validate",
    ]

    def __init__(self, subparsers) -> None:
        super().__init__(subparsers, "scenario", aliases=["sc"])
        self.init_subcommand_upload()
        self.init_subcommand_info()
        self.init_subcommand_delete()
        self.init_subcommand_validate()

    def init_subcommand_upload(self):
        parser = self.commands["upload"]
        parser.add_argument("--file", required=True, help="Path to scenario YAML file")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force overwrite if scenario metadata.name already exists (development only).",
        )
        parser.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            help="Print full values, including full multi-line command content.",
        )

    def init_subcommand_info(self):
        parser = self.commands["info"]
        parser.add_argument("resource", nargs="?", help="Scenario name, e.g. scenario-001")
        parser.add_argument("--id", dest="resource_id", type=int, help="Scenario id")
        parser.add_argument(
            "--validate",
            "--validation",
            dest="show_validation_details",
            action="store_true",
            help="Show detailed validation payload/history.",
        )

    def init_subcommand_delete(self):
        parser = self.commands["delete"]
        parser.add_argument("resource", help="Scenario name, e.g. scenario-001")

    def init_subcommand_validate(self):
        parser = self.commands["validate"]
        parser.add_argument("resource", help="Scenario name, e.g. scenario-001")
        parser.add_argument("-n", "--check-nodes", action="store_true", help="Check node connectivity via SSH.")
        parser.add_argument("-g", "--check-graph", action="store_true", help="Check graph connectivity ping for each scenario edge.")
        parser.add_argument("--phase", "--p", dest="phase_name", help="Validate only one phase template by name.")
        parser.add_argument("--edge", dest="edge_name", help="Validate/query only one graph edge by edge name.")
        parser.add_argument("--clean", action="store_true", help="Clear validation state/history for this scenario.")

    def _get_context(self):
        return IcopaConfig.get_current_config()

    def _parse_resource(self, resource: str) -> str:
        # Backward compatible: accept either "<name>" or "scene/<name>".
        scenario_name = resource.split("/", 1)[1].strip() if resource.startswith("scene/") else resource.strip()
        if not scenario_name:
            raise ValueError("Scenario name is required.")
        return scenario_name

    @staticmethod
    def _resolve_scenario_name_from_id(resource_id: int, scenario_items: list) -> str:
        for item in scenario_items:
            if not isinstance(item, dict):
                continue
            if item.get("id") != resource_id:
                continue
            scenario_name = str(item.get("name") or "").strip()
            if scenario_name:
                return scenario_name
            break
        raise ValueError(f"Scenario id {resource_id} was not found.")

    def _build_inventory_index(self) -> dict:
        config = self._get_context()
        success, data = self.call_api(
            "GET",
            f"{config['server'].rstrip('/')}/{Endpoints.INVENTORY}",
            auth_token=config.get("token"),
        )
        if not success or not isinstance(data, list):
            return {}
        return {
            item.get("name"): item
            for item in data
            if isinstance(item, dict) and item.get("name")
        }

    @staticmethod
    def _extract_graph_edges(scenario: dict) -> list[dict]:
        graph = scenario.get("graph") or {}
        edges = graph.get("edges") if isinstance(graph, dict) else []
        if not isinstance(edges, list):
            return []
        rendered: list[dict] = []
        for idx, edge in enumerate(edges, start=1):
            if not isinstance(edge, dict):
                continue
            from_name = edge.get("from")
            to_name = edge.get("to")
            if from_name and to_name:
                edge_name = str(edge.get("name") or f"edge-{idx}")
                rendered.append(
                    {
                        "name": edge_name,
                        "from": str(from_name),
                        "to": str(to_name),
                    }
                )
        return rendered

    @staticmethod
    def _extract_phase_summary(scenario: dict) -> list[tuple[str, list[str]]]:
        raw_payload = scenario.get("raw_payload") or {}
        if not isinstance(raw_payload, dict):
            return []
        spec = raw_payload.get("spec") or {}
        if not isinstance(spec, dict):
            return []
        phase_templates = spec.get("phaseTemplates")

        phase_rows: list[tuple[str, list[str]]] = []

        def render_target(item: dict) -> str:
            target = str(item.get("target") or "").strip()
            if target:
                return target
            target_ref = item.get("targetRef")
            if isinstance(target_ref, dict):
                kind = str(target_ref.get("kind") or "").strip()
                name = str(target_ref.get("name") or "").strip()
                if kind and name:
                    return f"{kind}:{name}"
            elif target_ref not in (None, ""):
                text = str(target_ref).strip()
                if text:
                    return text
            target_refs = item.get("targetRefs")
            if isinstance(target_refs, list):
                rendered_refs: list[str] = []
                for ref in target_refs:
                    if isinstance(ref, dict):
                        kind = str(ref.get("kind") or "").strip()
                        name = str(ref.get("name") or "").strip()
                        if kind and name:
                            rendered_refs.append(f"{kind}:{name}")
                    else:
                        text = str(ref or "").strip()
                        if text:
                            rendered_refs.append(text)
                if rendered_refs:
                    return ",".join(rendered_refs)
            return "-"

        def render_action(item: dict) -> str:
            action = str(item.get("type") or "unknown")
            if action == "run_runtime_preset":
                group = str(item.get("group") or "").strip()
                preset = str(item.get("preset") or "-")
                if not group and "/" in preset:
                    group = preset.split("/", 1)[0]
                    preset = preset.split("/", 1)[1]
                group = group or "-"
                return f"{action}:{group}/{preset}@{render_target(item)}"
            if action == "wait":
                params = item.get("parameters")
                seconds = params.get("seconds", 0) if isinstance(params, dict) else 0
                return f"wait({seconds}s)"
            target = render_target(item)
            return f"{action}@{target}" if target != "-" else action

        if isinstance(phase_templates, list):
            for phase in phase_templates:
                if not isinstance(phase, dict):
                    continue
                phase_name = str(phase.get("name") or "unnamed-phase")
                actions = phase.get("actions") or []
                rendered: list[str] = []
                if isinstance(actions, list):
                    for action_item in actions:
                        if isinstance(action_item, dict):
                            rendered.append(render_action(action_item))
                phase_rows.append((phase_name, rendered))
            return phase_rows
        return []

    @staticmethod
    def _parse_iso_datetime(raw_value) -> datetime | None:
        if not raw_value:
            return None
        text = str(raw_value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _format_before_label(cls, raw_value) -> str:
        parsed = cls._parse_iso_datetime(raw_value)
        if parsed is None:
            return "Before unknown time"
        delta_sec = int((datetime.now(timezone.utc) - parsed).total_seconds())
        if delta_sec < 0:
            delta_sec = 0
        minutes = delta_sec // 60
        if minutes < 1:
            return "Before <1 min"
        return f"Before {minutes} min"

    @staticmethod
    def _extract_last_checks(scenario: dict) -> tuple[dict[str, dict], dict[str, dict]]:
        last_validation_data = scenario.get("last_validation_data")
        checks = last_validation_data.get("checks") if isinstance(last_validation_data, dict) else {}
        if not isinstance(checks, dict):
            return {}, {}

        node_checks: dict[str, dict] = {}
        nodes_payload = checks.get("nodes")
        node_results = nodes_payload.get("results") if isinstance(nodes_payload, dict) else []
        if isinstance(node_results, list):
            for item in node_results:
                if not isinstance(item, dict):
                    continue
                node_name = str(item.get("node") or "")
                if not node_name:
                    continue
                node_checks[node_name] = {
                    "ok": bool(item.get("ok")),
                    "status": str(item.get("status") or ("SUCCESS" if item.get("ok") else "FAILED")),
                }

        graph_checks: dict[str, dict] = {}
        graph_payload = checks.get("graph")
        if isinstance(graph_payload, dict):
            graph_results = graph_payload.get("results") if isinstance(graph_payload.get("results"), list) else []
            for item in graph_results:
                if not isinstance(item, dict):
                    continue
                edge_name = str(item.get("edge_name") or "").strip()
                src = str(item.get("source_node") or "").strip()
                dst = str(item.get("target_node") or "").strip()
                key = edge_name or f"{src}->{dst}"
                if not key:
                    continue
                graph_checks[key] = item

            # Backward compatibility with old single-edge payload.
            if not graph_checks:
                graph_debug = graph_payload.get("debug") if isinstance(graph_payload.get("debug"), dict) else {}
                src = str(graph_debug.get("source_node") or "vm_home")
                dst = str(graph_debug.get("target_node") or "cloud_vm_bw")
                key = f"{src}->{dst}"
                graph_checks[key] = graph_payload
        return node_checks, graph_checks

    @classmethod
    def _format_vm_last_check_label(cls, vm: dict, fallback_raw=None) -> str:
        vm_time = vm.get("last_connection_time") or vm.get("updated_at")
        return cls._format_before_label(vm_time or fallback_raw)

    @classmethod
    def _format_managed_containers(cls, vm: dict, fallback_raw=None) -> str:
        managed = vm.get("managed_containers")
        if not isinstance(managed, list) or not managed:
            return ""

        default_label = cls._format_vm_last_check_label(vm, fallback_raw=fallback_raw)
        rendered: list[str] = []
        for item in managed:
            if isinstance(item, dict):
                name = str(item.get("name") or item.get("container") or item.get("id") or "").strip()
                if not name:
                    continue
                time_label = cls._format_before_label(
                    item.get("last_seen_at") or item.get("updated_at") or vm.get("last_connection_time") or fallback_raw
                )
                rendered.append(f"{name} ({time_label})")
            else:
                name = str(item).strip()
                if not name:
                    continue
                rendered.append(f"{name} ({default_label})")
        return ", ".join(rendered)

    def _print_scenario_info(self, scenario: dict, inventory_index: dict, show_validation_details: bool = False):
        print(f"Name:          {scenario.get('name', '')}")
        print(f"Kind:          {scenario.get('kind', '')}")
        print(f"Description:   {scenario.get('description', '')}")
        print(f"Created At:    {scenario.get('created_at', '')}")
        print(f"Updated At:    {scenario.get('updated_at', '')}")
        print("")
        print("Latest Validation Result:")
        print(f"- Status: {scenario.get('validation_status', 'IDLE')}")
        print(f"- Last Validation At: {scenario.get('last_validation_at', '')}")
        print(
            "- Check Status (actions/node/graph): "
            f"{scenario.get('check_status_actions', 'UNKNOWN')}/"
            f"{scenario.get('check_status_node', 'UNKNOWN')}/"
            f"{scenario.get('check_status_graph', 'UNKNOWN')}"
        )
        if (scenario.get("kind") or "") != "Scenario":
            print("Warning:       Resource kind is not 'Scenario'. Use `icopa runtime info` for runtime environments.")
        print("")
        if show_validation_details and scenario.get("last_validation_data"):
            print("Last Validation Data:")
            print(json.dumps(scenario.get("last_validation_data") or {}, indent=2, sort_keys=True))
            print("")
        last_check_label = self._format_before_label(scenario.get("last_validation_at"))
        node_checks, graph_checks = self._extract_last_checks(scenario)
        if show_validation_details:
            print("Validation:")
            history = scenario.get("validation_history") or []
            if not isinstance(history, list) or not history:
                print("(none)")
            else:
                nr = 1
                for run in history:
                    entries = run.get("entries") if isinstance(run, dict) else None
                    if not isinstance(entries, list):
                        continue
                    for item in entries:
                        if not isinstance(item, dict):
                            continue
                        action_name = str(item.get("action_name") or "unknown")
                        status = str(item.get("status") or "UNKNOWN")
                        print(f"- [{nr}] - {action_name}: {status}")
                        nr += 1
            print("")
            print("Validation Trace:")
            trace = scenario.get("validation_trace") or []
            if not isinstance(trace, list) or not trace:
                print("(none)")
            else:
                for idx, item in enumerate(trace, start=1):
                    if not isinstance(item, dict):
                        continue
                    step = str(item.get("step") or "unknown_step")
                    ts = str(item.get("time") or "")
                    print(f"- [{idx}] - {step}: {ts}")
            print("")
        print("Nodes:")
        nodes = scenario.get("nodes") or []
        if not isinstance(nodes, list) or not nodes:
            print("(none)")
        else:
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_name = node.get("name") or node.get("nodeName") or "-"
                ref_name = node.get("ref")
                inventory_name = ref_name or node_name
                vm = inventory_index.get(inventory_name) or inventory_index.get(node_name)
                in_system = "yes" if vm else "no"
                status = vm.get("status", "UNKNOWN") if vm else "NOT_FOUND"
                print(f"- {node_name}:")
                print(f"    - in_system: {in_system}")
                print(f"    - status: {status}")
                if isinstance(vm, dict):
                    print(f"    - last_check_time: {self._format_vm_last_check_label(vm, scenario.get('last_validation_at'))}")
                    managed_display = self._format_managed_containers(vm, scenario.get("last_validation_at"))
                    if managed_display:
                        print(f"    - managed (alive) container: {managed_display}")
                check_item = node_checks.get(str(node_name))
                if check_item is not None:
                    check_text = "Success" if check_item.get("ok") else "Failed"
                    print(f"    - last validation check: {check_text} ({last_check_label})")
        print("")
        print("Graph:")
        edges = self._extract_graph_edges(scenario)
        if not edges:
            print("(none)")
        else:
            for edge in edges:
                edge_name = str(edge.get("name") or "")
                src = str(edge.get("from") or "")
                dst = str(edge.get("to") or "")
                print(f"- {edge_name}: {src} --> {dst}")

                key = edge_name or f"{src}->{dst}"
                check_item = graph_checks.get(key)
                if check_item is None:
                    check_item = graph_checks.get(f"{src}->{dst}")
                if not isinstance(check_item, dict):
                    continue
                graph_ok = bool(check_item.get("ok")) if "ok" in check_item else None
                if graph_ok is None:
                    continue
                check_text = "Success" if graph_ok else "Failed"
                graph_metrics = check_item.get("metrics") if isinstance(check_item.get("metrics"), dict) else {}
                avg = graph_metrics.get("latency_avg_ms") if isinstance(graph_metrics, dict) else None
                if isinstance(avg, (int, float)):
                    print(f"      - last check: {check_text} | Ping latency: {avg:.2f} ms ({last_check_label})")
                    continue
                print(f"      - last check: {check_text} ({last_check_label})")
        print("")
        print("Runtime Env:")
        runtime_env = scenario.get("runtime_env") or []
        if not isinstance(runtime_env, list) or not runtime_env:
            print("(none)")
        else:
            for env in runtime_env:
                if isinstance(env, str):
                    print(f"- {env}")
                elif isinstance(env, dict):
                    print(f"- {env.get('name', '-')}")
                else:
                    print(f"- {env}")
        print("")
        print("Payloads:")
        payloads = scenario.get("payloads") or []
        if not isinstance(payloads, list) or not payloads:
            print("(none)")
        else:
            for payload in payloads:
                if isinstance(payload, dict):
                    print(f"- {payload.get('name', '-')}")
                else:
                    print(f"- {payload}")
        print("")
        print("Background Workloads:")
        workloads = scenario.get("background_workloads") or []
        if not isinstance(workloads, list) or not workloads:
            print("(none)")
        else:
            for workload in workloads:
                if isinstance(workload, dict):
                    print(f"- {workload.get('name', '-')}")
                else:
                    print(f"- {workload}")
        print("")
        print("Phases:")
        phase_rows = self._extract_phase_summary(scenario)
        if not phase_rows:
            print("(none)")
        else:
            for phase_name, actions in phase_rows:
                print(f"- {phase_name}: {actions if actions else []}")

    def list(self):
        config = self._get_context()
        success, data = self.call_api(
            "GET",
            f"{config['server'].rstrip('/')}/{Endpoints.SCENARIOS}",
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
                            len(item.get("nodes") or []),
                            len(item.get("runtime_env") or []),
                        ]
                    )
                print(tabulate(rows, headers=["ID", "NAME", "KIND", "NODES", "RUNTIME_ENV"], tablefmt="github"))
            else:
                print(data)

    def upload(self, *args):
        parser = self.commands["upload"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        with open(parsed.file, "rb") as handle:
            success, data = self.call_api(
                "POST",
                f"{config['server'].rstrip('/')}/{Endpoints.SCENARIOS}",
                data={"force": "true" if parsed.force else "false"},
                files={"file": (parsed.file, handle, "application/x-yaml")},
                auth_token=config.get("token"),
            )
        if success:
            self._print_upload_summary(data, verbose=parsed.verbose)

    def _print_upload_summary(self, data: dict, *, verbose: bool) -> None:
        if not isinstance(data, dict):
            print(data)
            return
        print(data.get("message", "Scenario upload request finished."))

        created = data.get("created") if isinstance(data.get("created"), dict) else {}
        updated = data.get("updated") if isinstance(data.get("updated"), dict) else {}
        created_scenarios = created.get("scenarios") if isinstance(created, dict) else []
        updated_scenarios = updated.get("scenarios") if isinstance(updated, dict) else []

        if isinstance(created_scenarios, list):
            for item in created_scenarios:
                if not isinstance(item, dict):
                    continue
                print(f"Created Scenario:    {item.get('name', '-')}")
                print(f"Kind:                {item.get('kind', '-')}")

        if isinstance(updated_scenarios, list) and updated_scenarios:
            self._print_updated_fields(updated_scenarios, entity_label="Scenario", verbose=verbose)
        elif not created_scenarios:
            print("No changes detected.")

        total = data.get("total") if isinstance(data.get("total"), dict) else {}
        if isinstance(total, dict):
            print(f"Total Scenarios:     {total.get('scenarios', '-')}")

    def info(self, *args):
        parser = self.commands["info"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        if parsed.resource and parsed.resource_id is not None:
            parser.error("Provide either <scenario_name> or --id, not both.")

        if parsed.resource_id is not None:
            success, scenario_items = self.call_api(
                "GET",
                f"{config['server'].rstrip('/')}/{Endpoints.SCENARIOS}",
                auth_token=config.get("token"),
            )
            if not success or not isinstance(scenario_items, list):
                parser.error("Unable to resolve scenario name from id.")
            try:
                scenario_name = self._resolve_scenario_name_from_id(parsed.resource_id, scenario_items)
            except ValueError as exc:
                parser.error(str(exc))
        else:
            if not parsed.resource:
                parser.error("Either <scenario_name> or --id is required.")
            try:
                scenario_name = self._parse_resource(parsed.resource)
            except ValueError as exc:
                parser.error(str(exc))
        success, data = self.call_api(
            "GET",
            f"{config['server'].rstrip('/')}/{Endpoints.SCENARIO_DETAIL}{scenario_name}/",
            auth_token=config.get("token"),
        )
        if success:
            inventory_index = self._build_inventory_index()
            self._print_scenario_info(data, inventory_index, show_validation_details=parsed.show_validation_details)

    def delete(self, *args):
        parser = self.commands["delete"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        try:
            scenario_name = self._parse_resource(parsed.resource)
        except ValueError as exc:
            parser.error(str(exc))
        success, data = self.call_api(
            "DELETE",
            f"{config['server'].rstrip('/')}/{Endpoints.SCENARIO_DETAIL}{scenario_name}/",
            auth_token=config.get("token"),
        )
        if success:
            print(data)

    def validate(self, *args):
        parser = self.commands["validate"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        try:
            scenario_name = self._parse_resource(parsed.resource)
        except ValueError as exc:
            parser.error(str(exc))

        if parsed.clean:
            success, data = self.call_api(
                "POST",
                f"{config['server'].rstrip('/')}/{Endpoints.SCENARIO_VALIDATE}{scenario_name}/validate/",
                json_data={"clean": True},
                auth_token=config.get("token"),
                timeout=60,
            )
            if success:
                print(json.dumps(data, indent=2, sort_keys=True))
            return

        check_nodes = parsed.check_nodes
        check_graph = parsed.check_graph
        if parsed.edge_name:
            check_nodes = False
            if parsed.phase_name:
                check_actions = True
                check_graph = True
            else:
                check_actions = False
                check_graph = True
        elif check_nodes:
            check_actions = False
            check_graph = False
        elif check_graph:
            check_actions = False
            check_nodes = False
        elif parsed.phase_name:
            # Phase validation/execution only.
            check_actions = True
            check_nodes = False
            check_graph = False
        else:
            check_actions = True
            check_nodes = True
            check_graph = True

        success, data = self.call_api(
            "POST",
            f"{config['server'].rstrip('/')}/{Endpoints.SCENARIO_VALIDATE}{scenario_name}/validate/",
            json_data={
                "check_actions": check_actions,
                "check_nodes": check_nodes,
                "check_graph": check_graph,
                "phase_name": parsed.phase_name,
                "edge_name": parsed.edge_name,
            },
            auth_token=config.get("token"),
            timeout=90,
        )
        if success:
            print(json.dumps(data, indent=2, sort_keys=True))

    def print_help(self):
        print(SCENARIO_HELP)
