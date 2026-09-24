"""Command group for experiment operations."""

from __future__ import annotations

import json

from tabulate import tabulate

from icopa_cli.command_group.base import CommandGroupBase
from icopa_cli.endpoints import Endpoints
from icopa_cli.icopa_config import IcopaConfig


EXPERIMENT_HELP = """
ICoPa CLI [exp] command group for experiment plan management

Usage:
    icopa exp <command> [-args]

Commands:
    list                List experiment plans
    upload              Create/update experiment plan from YAML file
    info                Show detailed information for one experiment plan
    delete              Delete one experiment plan
    gen                 Generate concrete runs from one experiment
    start               Start one generated pending run for an experiment
    run_check           Dry-run check for one experiment generated payload
    run_list            List execution runs
    run_info            Show one execution run details by id
    run_del             Delete execution run(s) by --id or experiment name

Resource:
    <exp_name>
"""


class ExperimentCommandGroup(CommandGroupBase):
    """Command group [exp]."""

    COMMAND_LIST = [
        "list",
        "upload",
        "info",
        "delete",
        "gen",
        "start",
        "run_check",
        "run_list",
        "run_info",
        "run_del",
    ]

    def __init__(self, subparsers) -> None:
        super().__init__(subparsers, "exp")
        self.init_subcommand_upload()
        self.init_subcommand_info()
        self.init_subcommand_delete()
        self.init_subcommand_gen()
        self.init_subcommand_start()
        self.init_subcommand_run_check()
        self.init_subcommand_run_list()
        self.init_subcommand_run_info()
        self.init_subcommand_run_del()

    def init_subcommand_upload(self):
        parser = self.commands["upload"]
        parser.add_argument("--file", required=True, help="Path to experiment plan YAML file")
        parser.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            help="Print full values, including full multi-line command content.",
        )

    def init_subcommand_info(self):
        parser = self.commands["info"]
        parser.add_argument("resource", nargs="?", help="Experiment name, e.g. exp_plan_two_vm_zenoh_single_run")
        parser.add_argument("--id", dest="resource_id", type=int, help="Experiment id")

    def init_subcommand_delete(self):
        parser = self.commands["delete"]
        parser.add_argument("resource", help="Experiment name, e.g. exp_plan_two_vm_zenoh_single_run")

    def init_subcommand_gen(self):
        parser = self.commands["gen"]
        parser.add_argument("resource", help="Experiment name to compile, e.g. exp_plan_two_vm_zenoh_single_run")
        parser.add_argument(
            "--export",
            help="Optional file path to export generated concrete YAML",
        )
        parser.add_argument(
            "--replace",
            dest="replace_run_id",
            type=int,
            help="Optional pending run id to replace with a newly generated run",
        )
        parser.add_argument(
            "--replace-gen-version",
            dest="replace_gen_version",
            type=int,
            help="Optional generation version to fully replace (delete old runs in that generation first)",
        )

    def init_subcommand_start(self):
        parser = self.commands["start"]
        parser.add_argument("resource", help="Experiment name, e.g. exp_plan_two_vm_zenoh_single_run")
        parser.add_argument("--gen-version", dest="gen_version", type=int, required=True, help="Required generation version")
        parser.add_argument("--id", dest="run_id", type=int, help="Optional run database id within generation")

    def init_subcommand_run_check(self):
        parser = self.commands["run_check"]
        parser.add_argument("resource", help="Experiment name, e.g. exp_plan_two_vm_zenoh_single_run")

    def init_subcommand_run_list(self):
        parser = self.commands["run_list"]
        parser.add_argument(
            "--exp",
            dest="exp_name",
            help="Optional experiment name filter",
        )
        parser.add_argument(
            "--gen-version",
            dest="gen_version",
            type=int,
            help="Optional generation version filter",
        )

    def init_subcommand_run_info(self):
        parser = self.commands["run_info"]
        parser.add_argument("--id", dest="run_id", type=int, required=True, help="Generated run id")

    def init_subcommand_run_del(self):
        parser = self.commands["run_del"]
        parser.add_argument("resource", nargs="?", help="Experiment name, e.g. exp_plan_two_vm_zenoh_single_run")
        parser.add_argument("--id", dest="run_id", type=int, help="Execution run id")

    def _get_context(self):
        return IcopaConfig.get_current_config()

    def _parse_resource(self, resource: str) -> str:
        exp_name = resource.split("/", 1)[1].strip() if resource.startswith("exp/") else resource.strip()
        if not exp_name:
            raise ValueError("Experiment name is required.")
        return exp_name

    @staticmethod
    def _resolve_experiment_name_from_id(resource_id: int, experiment_items: list) -> str:
        for item in experiment_items:
            if not isinstance(item, dict):
                continue
            if item.get("id") != resource_id:
                continue
            exp_name = str(item.get("name") or "").strip()
            if exp_name:
                return exp_name
            break
        raise ValueError(f"Experiment id {resource_id} was not found.")

    def _print_experiment_info(self, exp: dict):
        print(f"Name:          {exp.get('name', '')}")
        print(f"Kind:          {exp.get('kind', '')}")
        print(f"Description:   {exp.get('description', '')}")
        print(f"Scenario:      {exp.get('scenario_name', '')}")
        print(f"Status:        {exp.get('status', '')}")
        print(f"Start Count:   {exp.get('start_count', 0)}")
        print(f"Last Started:  {exp.get('last_started_at', '')}")
        print(f"Generated:     {'yes' if exp.get('has_generated_plan') else 'no'}")
        print(f"Current Gen:   {exp.get('current_gen_version', 0)}")
        print(f"Generated At:  {exp.get('generated_at', '')}")
        print(f"Run Check:     {exp.get('run_check_status', '')}")
        print(f"Checked At:    {exp.get('run_checked_at', '')}")
        print(f"Created At:    {exp.get('created_at', '')}")
        print(f"Updated At:    {exp.get('updated_at', '')}")
        print("")
        print("Execution Status:")
        status = exp.get("execution_status") if isinstance(exp.get("execution_status"), dict) else {}
        by_generation = status.pop("by_generation", None)
        print(json.dumps(status, indent=2, sort_keys=True))
        if isinstance(by_generation, list) and by_generation:
            print("")
            print("Generation Status:")
            rows = []
            for item in by_generation:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    [
                        item.get("gen_version", ""),
                        item.get("total", 0),
                        item.get("pending", 0),
                        item.get("running", 0),
                        item.get("succeeded", 0),
                        item.get("failed", 0),
                    ]
                )
            if rows:
                print(tabulate(rows, headers=["GEN", "TOTAL", "PENDING", "RUNNING", "SUCCEEDED", "FAILED"], tablefmt="github"))
        print("")
        print("Execution:")
        print(json.dumps(exp.get("execution") or {}, indent=2, sort_keys=True))
        print("")
        print("Selections:")
        print(json.dumps(exp.get("selections") or {}, indent=2, sort_keys=True))
        print("")
        print("Phases:")
        phases = exp.get("phases") or []
        print(json.dumps(phases, indent=2, sort_keys=True))

    @staticmethod
    def _print_run_summaries(run_summaries: list[dict]):
        if not isinstance(run_summaries, list) or not run_summaries:
            print("Phases: []")
            return
        print("Phases:")
        first_run = run_summaries[0] if isinstance(run_summaries[0], dict) else {}
        phases = first_run.get("phases") if isinstance(first_run.get("phases"), list) else []
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            print(f"- {phase.get('name', 'unnamed-phase')}: {phase.get('actions') or []}")

    @classmethod
    def _print_rrt_variable_checks(cls, checks: dict):
        if not isinstance(checks, dict):
            return
        runs = checks.get("runs")
        if not isinstance(runs, list) or not runs:
            return
        print("")
        print("RRT Variable Checks:")
        rows = []
        for item in runs:
            if not isinstance(item, dict):
                continue
            rows.append(
                [
                    item.get("run_id", ""),
                    item.get("status", ""),
                    cls._short_text(
                        "; ".join([str(msg) for msg in item.get("messages") or []]),
                        max_len=80,
                    ),
                    cls._short_text(
                        ", ".join([str(path) for path in item.get("missing_paths") or []]),
                        max_len=80,
                    ),
                ]
            )
        if rows:
            print(tabulate(rows, headers=["RUN_ID", "STATUS", "MESSAGES", "MISSING_PATHS"], tablefmt="github"))

    @staticmethod
    def _render_compiled_action(action: dict) -> str:
        action_type = str(action.get("type") or action.get("action") or "unknown")
        if action_type == "run_runtime_preset":
            preset = str(action.get("preset") or "-")
            return f"{action_type}:{preset}@{action.get('target', '-')}"
        if action_type == "wait":
            params = action.get("parameters")
            seconds = params.get("seconds", 0) if isinstance(params, dict) else 0
            return f"wait({seconds}s)"
        if action_type == "collect_metrics":
            return "collect_metrics"
        target = action.get("target")
        return f"{action_type}@{target}" if target else action_type

    @classmethod
    def _run_summaries_from_compiled_payload(cls, compiled_payload: dict) -> list[dict]:
        runs = compiled_payload.get("runs") if isinstance(compiled_payload.get("runs"), list) else []
        summaries: list[dict] = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            phases = run.get("phases") if isinstance(run.get("phases"), list) else []
            rendered_phases = []
            for phase in phases:
                if not isinstance(phase, dict):
                    continue
                actions = phase.get("actions") if isinstance(phase.get("actions"), list) else []
                rendered_phases.append(
                    {
                        "name": str(phase.get("name") or "unnamed-phase"),
                        "actions": [
                            cls._render_compiled_action(action)
                            for action in actions
                            if isinstance(action, dict)
                        ],
                    }
                )
            summaries.append(
                {
                    "run_id": str(run.get("run_id") or ""),
                    "variant_values": run.get("variant_values") if isinstance(run.get("variant_values"), dict) else {},
                    "phases": rendered_phases,
                }
            )
        return summaries

    @staticmethod
    def _short_text(value, max_len: int = 100) -> str:
        text = str(value or "").strip()
        if len(text) <= max_len:
            return text
        return f"{text[: max_len - 3]}..."

    @staticmethod
    def _count_status(items: list[dict], key: str = "status") -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            status = str(item.get(key) or "UNKNOWN").upper()
            counts[status] = counts.get(status, 0) + 1
        return counts

    @classmethod
    def _print_run_tracker_summary(cls, tracker: dict):
        if not isinstance(tracker, dict):
            print("Execution Progress:")
            print("(not available)")
            return

        runs = tracker.get("runs")
        if not isinstance(runs, dict) or not runs:
            print("Execution Progress:")
            print("(not available)")
            return

        print("Execution Progress:")
        rows = []
        ordered_items = sorted(runs.items(), key=lambda item: str(item[0]))
        for run_id, run_item in ordered_items:
            run = run_item if isinstance(run_item, dict) else {}
            phases = run.get("phases") if isinstance(run.get("phases"), list) else []
            phase_counts = cls._count_status([item for item in phases if isinstance(item, dict)])
            actions: list[dict] = []
            for phase in phases:
                if not isinstance(phase, dict):
                    continue
                phase_actions = phase.get("actions") if isinstance(phase.get("actions"), list) else []
                actions.extend([item for item in phase_actions if isinstance(item, dict)])
            action_counts = cls._count_status(actions)
            rows.append(
                [
                    run_id,
                    run.get("status", ""),
                    run.get("runtime_env_name", ""),
                    f"{phase_counts.get('SUCCEEDED', 0)}/{len(phases)}",
                    f"{action_counts.get('SUCCEEDED', 0)}/{len(actions)}",
                    action_counts.get("FAILED", 0),
                    cls._short_text(run.get("error", ""), max_len=60),
                ]
            )

        print(
            tabulate(
                rows,
                headers=["RUN_ID", "STATUS", "RUNTIME_ENV", "PHASES_OK", "ACTIONS_OK", "ACTIONS_FAIL", "ERROR"],
                tablefmt="github",
            )
        )

        print("")
        print("Phase Details:")
        for run_id, run_item in ordered_items:
            run = run_item if isinstance(run_item, dict) else {}
            print(f"- {run_id} [{run.get('status', '')}]")
            phases = run.get("phases") if isinstance(run.get("phases"), list) else []
            for phase in phases:
                if not isinstance(phase, dict):
                    continue
                actions = phase.get("actions") if isinstance(phase.get("actions"), list) else []
                action_counts = cls._count_status([item for item in actions if isinstance(item, dict)])
                print(
                    "  "
                    + f"phase#{phase.get('index', '?')} {phase.get('name', '')}: {phase.get('status', '')} "
                    + f"(ok {action_counts.get('SUCCEEDED', 0)}/{len(actions)}, fail {action_counts.get('FAILED', 0)})"
                )
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    if str(action.get("status") or "").upper() != "FAILED":
                        continue
                    print(
                        "    "
                        + f"action#{action.get('index', '?')} {action.get('type', '')}@{action.get('target', '')}: "
                        + cls._short_text(action.get("error") or action.get("message") or "failed", max_len=120)
                    )

    @classmethod
    def _print_recent_events(cls, tracker: dict):
        events = tracker.get("events") if isinstance(tracker, dict) else None
        if not isinstance(events, list) or not events:
            return
        print("")
        print("Recent Events:")
        for item in events[-12:]:
            if not isinstance(item, dict):
                continue
            time_text = str(item.get("time") or "")
            event = str(item.get("event") or "")
            run_id = str(item.get("run_id") or "")
            phase = str(item.get("phase_name") or "")
            action_idx = str(item.get("action_index") or "")
            status = str(item.get("status") or "")
            error = cls._short_text(item.get("error") or "", max_len=80)
            parts = [part for part in [run_id, phase, f"action#{action_idx}" if action_idx else "", status] if part]
            meta = " ".join(parts)
            if error:
                meta = f"{meta} err={error}".strip()
            print(f"- {time_text} {event} {meta}".strip())

    @classmethod
    def _print_result_summary(cls, results: list[dict]):
        if not isinstance(results, list) or not results:
            print("")
            print("Result Summary:")
            print("(not available)")
            return
        print("")
        print("Result Summary:")
        rows = []
        for run in results:
            if not isinstance(run, dict):
                continue
            phases = run.get("phases") if isinstance(run.get("phases"), list) else []
            phase_counts = cls._count_status([item for item in phases if isinstance(item, dict)])
            rows.append(
                [
                    run.get("run_id", ""),
                    run.get("status", ""),
                    run.get("runtime_env_name", ""),
                    phase_counts.get("SUCCEEDED", 0),
                    phase_counts.get("FAILED", 0),
                    cls._short_text(run.get("error", ""), max_len=60),
                ]
            )
        if rows:
            print(
                tabulate(
                    rows,
                    headers=["RUN_ID", "STATUS", "RUNTIME_ENV", "PHASE_OK", "PHASE_FAIL", "ERROR"],
                    tablefmt="github",
                )
            )
        else:
            print("(not available)")

    @classmethod
    def _print_collected_metrics_summary(cls, *, collected_metrics_path: str, results: list[dict]):
        print("")
        print("Collected Metrics:")
        print(f"- Backend Base Path: {collected_metrics_path or '(not set)'}")
        if not isinstance(results, list) or not results:
            print("- Collection Actions: (not available)")
            return

        rows = []
        for run in results:
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("run_id") or "")
            phases = run.get("phases") if isinstance(run.get("phases"), list) else []
            for phase in phases:
                if not isinstance(phase, dict):
                    continue
                actions = phase.get("actions") if isinstance(phase.get("actions"), list) else []
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    if str(action.get("type") or "") != "collect_metrics":
                        continue
                    debug = action.get("debug") if isinstance(action.get("debug"), dict) else {}
                    pull_result = (
                        debug.get("artifact_pull_result")
                        if isinstance(debug.get("artifact_pull_result"), dict)
                        else {}
                    )
                    errors = pull_result.get("errors") if isinstance(pull_result.get("errors"), list) else []
                    rows.append(
                        [
                            run_id,
                            phase.get("name", ""),
                            action.get("status", ""),
                            debug.get("metrics_remote_dir", ""),
                            debug.get("metrics_local_base_dir", ""),
                            cls._short_text(action.get("message", ""), max_len=90),
                            cls._short_text("; ".join([str(item) for item in errors]), max_len=90),
                        ]
                    )

        if not rows:
            print("- Collection Actions: (none)")
            return
        print(
            tabulate(
                rows,
                headers=["RUN_ID", "PHASE", "STATUS", "REMOTE_DIR", "BACKEND_DIR", "MESSAGE", "ERRORS"],
                tablefmt="github",
            )
        )

    @staticmethod
    def _backend_dir_to_static_url(path_text: str) -> str:
        text = str(path_text or "").strip().replace("\\", "/")
        if not text:
            return ""
        if text.startswith("/"):
            return text
        if text.startswith("static/"):
            return f"/{text}"
        return f"/static/{text}"

    @staticmethod
    def _variant_values_summary(variant_values: dict) -> str:
        if not isinstance(variant_values, dict) or not variant_values:
            return "(none)"
        items = []
        for key in sorted(variant_values.keys()):
            items.append(f"{key}={variant_values.get(key)}")
        return ", ".join(items)

    @staticmethod
    def _collect_action_succeeded(run_result: dict) -> bool:
        phases = run_result.get("phases") if isinstance(run_result.get("phases"), list) else []
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            actions = phase.get("actions") if isinstance(phase.get("actions"), list) else []
            for action in actions:
                if not isinstance(action, dict):
                    continue
                if str(action.get("type") or "") != "collect_metrics":
                    continue
                if str(action.get("status") or "").upper() == "SUCCESS":
                    return True
        return False

    @classmethod
    def _print_per_run_metrics_details(
        cls,
        *,
        results: list[dict],
        compiled_runs: list[dict],
        base_metrics_url: str,
    ) -> None:
        plan_by_run_id: dict[str, dict] = {}
        for run in compiled_runs:
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("run_id") or "").strip()
            if not run_id:
                continue
            plan_by_run_id[run_id] = run

        result_by_run_id: dict[str, dict] = {}
        for run in results:
            if not isinstance(run, dict):
                continue
            run_id = str(run.get("run_id") or "").strip()
            if not run_id:
                continue
            result_by_run_id[run_id] = run

        run_ids = sorted(set(plan_by_run_id.keys()) | set(result_by_run_id.keys()))
        if not run_ids:
            return

        print("")
        print("Run Metrics Detail:")
        for run_id in run_ids:
            plan_item = plan_by_run_id.get(run_id) if isinstance(plan_by_run_id.get(run_id), dict) else {}
            result_item = result_by_run_id.get(run_id) if isinstance(result_by_run_id.get(run_id), dict) else {}
            variant_values = (
                result_item.get("variant_values")
                if isinstance(result_item.get("variant_values"), dict)
                else plan_item.get("variant_values")
            )
            metrics_url = cls._backend_dir_to_static_url(str(result_item.get("metrics_backend_dir") or ""))
            if not metrics_url and base_metrics_url:
                metrics_url = f"{base_metrics_url.rstrip('/')}/{run_id}"
            metrics_collected = cls._collect_action_succeeded(result_item) if result_item else False
            error_text = str(result_item.get("error") or "").strip() if result_item else ""
            planned_collect_targets: list[str] = []
            planned_collect_remote_dirs: list[str] = []
            phases = plan_item.get("phases") if isinstance(plan_item.get("phases"), list) else []
            for phase in phases:
                if not isinstance(phase, dict):
                    continue
                actions = phase.get("actions") if isinstance(phase.get("actions"), list) else []
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    if str(action.get("type") or "") != "collect_metrics":
                        continue
                    target = str(action.get("target") or "").strip()
                    if target:
                        planned_collect_targets.append(target)
                    params = action.get("parameters") if isinstance(action.get("parameters"), dict) else {}
                    remote_dir = str(params.get("remote_dir") or "").strip()
                    if remote_dir:
                        planned_collect_remote_dirs.append(remote_dir)
            dedup_targets = sorted({item for item in planned_collect_targets if item})
            dedup_remote_dirs = sorted({item for item in planned_collect_remote_dirs if item})
            desired_collect_vm = ", ".join(dedup_targets) if dedup_targets else "(not set)"
            if dedup_remote_dirs:
                desired_remote_dir = ", ".join(dedup_remote_dirs)
            else:
                run_remote = str(result_item.get("metrics_remote_dir") or "").strip() if result_item else ""
                desired_remote_dir = run_remote or "(auto at runtime under per-run metrics remote dir)"

            print(f"- {run_id}:")
            print(f"  - Parameters: {cls._variant_values_summary(variant_values if isinstance(variant_values, dict) else {})}")
            print(f"  - Metrics URL: {metrics_url or '(not set)'}")
            print(f"  - Desired Collect VM: {desired_collect_vm}")
            print(f"  - Desired Remote Path On VM: {desired_remote_dir}")
            print(f"  - Metrics Collected: {'true' if metrics_collected else 'false'}")
            print(f"  - Error: {error_text or '(none)'}")

    def list(self):
        config = self._get_context()
        success, data = self.call_api(
            "GET",
            f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_EXPS}",
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
                            item.get("scenario_name"),
                            item.get("status"),
                            "yes" if item.get("has_generated_plan") else "no",
                            len(item.get("phases") or []),
                        ]
                    )
                print(tabulate(rows, headers=["ID", "NAME", "SCENARIO", "STATUS", "GENERATED", "PHASES"], tablefmt="github"))
            else:
                print(data)

    def upload(self, *args):
        parser = self.commands["upload"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        with open(parsed.file, "rb") as handle:
            success, data = self.call_api(
                "POST",
                f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_EXPS}",
                files={"file": (parsed.file, handle, "application/x-yaml")},
                auth_token=config.get("token"),
            )
        if success:
            self._print_update_summary(data, verbose=parsed.verbose)

    def _print_update_summary(self, data: dict, *, verbose: bool) -> None:
        if not isinstance(data, dict):
            print(data)
            return
        print(data.get("message", "Experiment update request finished."))

        created = data.get("created") if isinstance(data.get("created"), dict) else {}
        updated = data.get("updated") if isinstance(data.get("updated"), dict) else {}
        created_items = created.get("experiments") if isinstance(created, dict) else []
        updated_items = updated.get("experiments") if isinstance(updated, dict) else []

        if isinstance(created_items, list):
            for item in created_items:
                if not isinstance(item, dict):
                    continue
                print(f"Created Experiment:  {item.get('name', '-')}")
                print(f"Scenario:            {item.get('scenario_name', '-')}")

        if isinstance(updated_items, list) and updated_items:
            self._print_updated_fields(updated_items, entity_label="Experiment", verbose=verbose)
        elif not created_items:
            print("No changes detected.")

        invalidated = data.get("invalidated") if isinstance(data.get("invalidated"), dict) else {}
        if isinstance(invalidated, dict):
            print(f"Invalidated Runs:    {invalidated.get('runs', 0)}")

        total = data.get("total") if isinstance(data.get("total"), dict) else {}
        if isinstance(total, dict):
            print(f"Total Experiments:   {total.get('experiments', '-')}")

    def info(self, *args):
        parser = self.commands["info"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        if parsed.resource and parsed.resource_id is not None:
            parser.error("Provide either <experiment_name> or --id, not both.")

        if parsed.resource_id is not None:
            success, experiment_items = self.call_api(
                "GET",
                f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_EXPS}",
                auth_token=config.get("token"),
            )
            if not success or not isinstance(experiment_items, list):
                parser.error("Unable to resolve experiment name from id.")
            try:
                exp_name = self._resolve_experiment_name_from_id(parsed.resource_id, experiment_items)
            except ValueError as exc:
                parser.error(str(exc))
        else:
            if not parsed.resource:
                parser.error("Either <experiment_name> or --id is required.")
            try:
                exp_name = self._parse_resource(parsed.resource)
            except ValueError as exc:
                parser.error(str(exc))
        success, data = self.call_api(
            "GET",
            f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_EXP_DETAIL}{exp_name}/",
            auth_token=config.get("token"),
        )
        if success:
            self._print_experiment_info(data)
            if not data.get("has_generated_plan"):
                return
            compiled_payload = data.get("generated_payload") if isinstance(data.get("generated_payload"), dict) else {}
            print("")
            print("Generated Payload:")
            print(f"Total Runs: {compiled_payload.get('total_generated_runs', 0)}")
            self._print_run_summaries(self._run_summaries_from_compiled_payload(compiled_payload))

    def delete(self, *args):
        parser = self.commands["delete"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        try:
            exp_name = self._parse_resource(parsed.resource)
        except ValueError as exc:
            parser.error(str(exc))
        success, data = self.call_api(
            "DELETE",
            f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_EXP_DETAIL}{exp_name}/",
            auth_token=config.get("token"),
        )
        if success:
            print(data)

    def gen(self, *args):
        parser = self.commands["gen"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        try:
            exp_name = self._parse_resource(parsed.resource)
        except ValueError as exc:
            parser.error(str(exc))

        payload = {}
        if parsed.replace_run_id is not None and parsed.replace_gen_version is not None:
            parser.error("Use either --replace or --replace-gen-version, not both.")
        if parsed.replace_run_id is not None:
            payload["replace_run_id"] = parsed.replace_run_id
        if parsed.replace_gen_version is not None:
            payload["replace_gen_version"] = parsed.replace_gen_version
        success, data = self.call_api(
            "POST",
            f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_EXP_GENERATE}{exp_name}/generate/",
            json_data=payload,
            auth_token=config.get("token"),
        )
        if not success:
            return

        print(f"Experiment: {exp_name}")
        print(f"Generation: {data.get('gen_version', 0)}")
        if parsed.replace_gen_version is not None:
            print(f"Replace Mode: gen_version=v{parsed.replace_gen_version}")
        elif parsed.replace_run_id is not None:
            print(f"Replace Mode: run_id={parsed.replace_run_id}")
            print(f"Replaced Plan Run: {data.get('replaced_plan_run_id', '') or '-'}")
        pending_runs = data.get("pending_runs") if isinstance(data, dict) and isinstance(data.get("pending_runs"), list) else []
        print(f"Created Pending Runs: {len(pending_runs)}")
        if pending_runs:
            print("Pending Run IDs: " + ", ".join([str(item.get("id")) for item in pending_runs if isinstance(item, dict)]))
        print(f"Total Runs: {data.get('total_generated_runs', 0)}")
        self._print_run_summaries(data.get("run_summaries") if isinstance(data, dict) else [])
        self._print_rrt_variable_checks(data.get("rrt_variable_checks") if isinstance(data, dict) else {})

        if parsed.export:
            compiled_yaml = data.get("compiled_yaml") if isinstance(data, dict) else ""
            with open(parsed.export, "w", encoding="utf-8") as handle:
                handle.write(str(compiled_yaml or ""))
            print(f"Exported: {parsed.export}")

    def start(self, *args):
        parser = self.commands["start"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        exp_name = self._parse_resource(parsed.resource)
        payload = {"gen_version": parsed.gen_version}
        if parsed.run_id is not None:
            payload["run_id"] = parsed.run_id
        success, data = self.call_api(
            "POST",
            f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_EXP_START}{exp_name}/start/",
            json_data=payload,
            auth_token=config.get("token"),
        )
        if success:
            if not isinstance(data, dict):
                print(data)
                return
            print(f"Message:            {data.get('message', '')}")
            print(f"Generation:         {data.get('gen_version', '')}")
            print(f"Started Run ID:     {data.get('started_run_id', '')}")
            print(f"Queued Count:       {data.get('queued_count', 0)}")
            print(f"Fallback Count:     {data.get('sync_fallback_count', 0)}")
            print(f"Remaining Pending:  {data.get('remaining_pending_runs', 0)}")

    def run_check(self, *args):
        parser = self.commands["run_check"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        exp_name = self._parse_resource(parsed.resource)
        success, data = self.call_api(
            "POST",
            f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_EXP_RUN_CHECK}{exp_name}/run_check/",
            auth_token=config.get("token"),
        )
        if not success or not isinstance(data, dict):
            return

        print(f"Experiment: {data.get('experiment_name', exp_name)}")
        print(f"Check Status: {'PASS' if data.get('ok') else 'FAIL'}")
        print(f"Run Check Tag: {data.get('run_check_status', '')}")
        print(f"Checked At: {data.get('run_checked_at', '')}")
        print(f"Total Runs: {data.get('total_runs', 0)}")
        print(f"Failed Runs: {data.get('failed_runs', 0)}")
        self._print_run_summaries(data.get("run_summaries") if isinstance(data, dict) else [])

        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            print("Errors:")
            for item in errors:
                print(f"- {item}")

    def run_list(self, *args):
        parser = self.commands["run_list"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        url = f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_RUNS}"
        query_parts = []
        if parsed.exp_name:
            query_parts.append(f"exp_name={parsed.exp_name}")
        if parsed.gen_version is not None:
            query_parts.append(f"gen_version={parsed.gen_version}")
        if query_parts:
            url = f"{url}?{'&'.join(query_parts)}"
        success, data = self.call_api(
            "GET",
            url,
            auth_token=config.get("token"),
        )
        if not success:
            return
        if not isinstance(data, list) or not data:
            print("[]")
            return
        rows = []
        for item in data:
            if not isinstance(item, dict):
                continue
            rows.append(
                [
                    item.get("id"),
                    item.get("gen_version", ""),
                    item.get("plan_run_id"),
                    item.get("experiment_name"),
                    item.get("status"),
                    item.get("total_runs", 0),
                    item.get("completed_runs", 0),
                    item.get("failed_runs", 0),
                    item.get("task_id", ""),
                    item.get("created_at", ""),
                ]
            )
        print(
            tabulate(
                rows,
                headers=["RUN_DB_ID", "GEN", "PLAN_RUN_ID", "EXPERIMENT", "STATUS", "TOTAL", "DONE", "FAILED", "TASK_ID", "CREATED_AT"],
                tablefmt="github",
            )
        )

    def run_info(self, *args):
        parser = self.commands["run_info"]
        parsed = parser.parse_args(args)
        config = self._get_context()
        success, data = self.call_api(
            "GET",
            f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_RUN_DETAIL}{parsed.run_id}/",
            auth_token=config.get("token"),
        )
        if not success or not isinstance(data, dict):
            return
        print(f"Run ID:         {data.get('id', '')}")
        print(f"Generation:     {data.get('gen_version', '')}")
        print(f"Plan Run ID:    {data.get('plan_run_id', '')}")
        print(f"Experiment:     {data.get('experiment_name', '')}")
        print(f"Status:         {data.get('status', '')}")
        print(f"Task ID:        {data.get('task_id', '')}")
        print(f"Total Runs:     {data.get('total_runs', 0)}")
        print(f"Completed:      {data.get('completed_runs', 0)}")
        print(f"Failed:         {data.get('failed_runs', 0)}")
        print(f"Stop On Failure: {'true' if data.get('stop_on_failure') else 'false'}")
        print(f"Started At:     {data.get('started_at', '')}")
        print(f"Finished At:    {data.get('finished_at', '')}")
        print(f"Created At:     {data.get('created_at', '')}")
        print(f"Updated At:     {data.get('updated_at', '')}")
        print(f"Metrics Path:   {data.get('collected_metrics_path', '')}")
        print(f"Metrics URL:    {data.get('collected_metrics_url', '')}")
        print(f"Metrics Collected: {'true' if data.get('metrics_collected') else 'false'}")
        print(f"Error Message:  {self._short_text(data.get('error_message', ''), max_len=120)}")
        tracker = data.get("phase_action_execution_status") if isinstance(data.get("phase_action_execution_status"), dict) else {}
        results = data.get("results") if isinstance(data.get("results"), list) else []
        compiled_payload = data.get("compiled_payload_snapshot") if isinstance(data.get("compiled_payload_snapshot"), dict) else {}
        compiled_runs = compiled_payload.get("runs") if isinstance(compiled_payload.get("runs"), list) else []
        self._print_run_tracker_summary(tracker)
        self._print_recent_events(tracker)
        self._print_result_summary(results)
        self._print_collected_metrics_summary(
            collected_metrics_path=str(data.get("collected_metrics_path") or ""),
            results=results,
        )
        self._print_per_run_metrics_details(
            results=results,
            compiled_runs=compiled_runs,
            base_metrics_url=str(data.get("collected_metrics_url") or ""),
        )
        snapshots = data.get("rrt_config_execution_snapshot") if isinstance(data.get("rrt_config_execution_snapshot"), dict) else {}
        runs_snapshot = snapshots.get("runs") if isinstance(snapshots.get("runs"), dict) else {}
        if runs_snapshot:
            print("")
            print("RRT Config Snapshot:")
            rows = []
            for run_id in sorted(runs_snapshot.keys()):
                run_item = runs_snapshot.get(run_id) if isinstance(runs_snapshot.get(run_id), dict) else {}
                actions = run_item.get("actions") if isinstance(run_item.get("actions"), list) else []
                rows.append(
                    [
                        run_id,
                        run_item.get("runtime_env_name", ""),
                        "yes" if run_item.get("runtime_has_rrt_config") else "no",
                        len(actions),
                    ]
                )
            print(tabulate(rows, headers=["RUN_ID", "RUNTIME_ENV", "HAS_BASE_RRT", "PROFILING_ACTIONS"], tablefmt="github"))

    def run_del(self, *args):
        parser = self.commands["run_del"]
        parsed = parser.parse_args(args)
        if parsed.run_id is None and not parsed.resource:
            parser.error("Provide either --id <run_id> or <exp_name>.")
        if parsed.run_id is not None and parsed.resource:
            parser.error("Provide only one target: --id <run_id> or <exp_name>.")

        config = self._get_context()
        if parsed.run_id is not None:
            success, data = self.call_api(
                "DELETE",
                f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_RUN_DETAIL}{parsed.run_id}/",
                auth_token=config.get("token"),
            )
            if success and isinstance(data, dict):
                print(f"Message:            {data.get('message', '')}")
                print(f"Deleted Run ID:     {data.get('deleted_run_id', '')}")
                print(f"Deleted Dirs:       {len(data.get('deleted_artifact_dirs') or [])}")
                print(f"Missing Dirs:       {len(data.get('missing_artifact_dirs') or [])}")
            return

        exp_name = self._parse_resource(parsed.resource)
        success, data = self.call_api(
            "DELETE",
            f"{config['server'].rstrip('/')}/{Endpoints.PROFILING_EXP_RUNS}{exp_name}/runs/",
            auth_token=config.get("token"),
        )
        if success:
            if not isinstance(data, dict):
                print(data)
                return
            print(f"Message:            {data.get('message', '')}")
            print(f"Experiment:         {data.get('experiment_name', '')}")
            print(f"Total Found:        {data.get('total_found', 0)}")
            print(f"Deleted Runs:       {len(data.get('deleted_run_ids') or [])}")
            print(f"Skipped Active:     {len(data.get('skipped_active_run_ids') or [])}")
            print(f"Deleted Dirs:       {len(data.get('deleted_artifact_dirs') or [])}")
            print(f"Missing Dirs:       {len(data.get('missing_artifact_dirs') or [])}")
            errors = data.get("errors") or []
            print(f"Errors:             {len(errors)}")
            if errors:
                print("")
                print("Error Details:")
                for item in errors:
                    print(f"- {item}")

    def print_help(self):
        print(EXPERIMENT_HELP)
