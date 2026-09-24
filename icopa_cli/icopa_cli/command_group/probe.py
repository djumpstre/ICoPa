"""Thin CLI for Hub-managed online latency measurements."""

import json
from urllib.parse import urlencode

from icopa_cli.icopa_config import IcopaConfig
from .base import CommandGroupBase


class ProbeCommandGroup(CommandGroupBase):
    COMMAND_LIST = ["start", "list", "info", "stop", "lookup"]

    def __init__(self, subparsers):
        super().__init__(subparsers, "probe")
        start = self.commands["start"]
        start.add_argument("source_vm", type=int)
        start.add_argument("target_vm", type=int)
        start.add_argument("--duration-sec", type=int, default=5)
        start.add_argument("--interval-sec", type=int, default=10)
        start.add_argument("--window-count", type=int, default=6)
        for name in ("info", "stop"):
            self.commands[name].add_argument("session_id", type=int)
        lookup = self.commands["lookup"]
        lookup.add_argument("--source-vm", type=int)
        lookup.add_argument("--target-vm", type=int)
        lookup.add_argument("--max-age-sec", type=int, default=60)
        for parser in self.commands.values():
            parser.add_argument("--json", action="store_true")

    def request(self, method, path, payload=None):
        server = IcopaConfig.get_current_config()["server"].rstrip("/")
        _, data = self.call_api(method, f"{server}/micro_probing/{path}", json_data=payload)
        return data

    @staticmethod
    def display(data, raw=False):
        if raw:
            print(json.dumps(data, indent=2))
            return
        rows = data if isinstance(data, list) else [data]
        for row in rows:
            print(f"Session {row['id']}: {row['status']} | VM {row['source_vm']} -> VM {row['target_vm']}")
            print(f"Created: {row.get('created_at')} | Started: {row.get('started_at')} | Finished: {row.get('finished_at')}")
            print(f"Windows requested: {row['window_count']} | Duration: {row['duration_sec']}s | Gap: {row['interval_sec']}s")
            if row.get("error_message"):
                print(f"Failure: {row['error_message']}")
            for window in row.get("windows", []):
                metrics = window["metrics"]
                avg = metrics.get("latency_ms", {}).get("avg")
                print(f"  Window {window['sequence']}: RTT={avg} ms | loss={metrics.get('loss_percent')}% | measured={window['measured_at']}")
                if window.get("error_message"):
                    print(f"  Failure: {window['error_message']}")
        if not rows:
            print("No probe sessions.")

    def start(self, *args):
        options = self.commands["start"].parse_args(args)
        payload = {key: value for key, value in vars(options).items() if key != "json"}
        self.display(self.request("POST", "sessions/", payload), options.json)

    def list(self, *args):
        options = self.commands["list"].parse_args(args)
        self.display(self.request("GET", "sessions/"), options.json)

    def info(self, *args):
        options = self.commands["info"].parse_args(args)
        self.display(self.request("GET", f"sessions/{options.session_id}/"), options.json)

    def stop(self, *args):
        options = self.commands["stop"].parse_args(args)
        self.display(self.request("POST", f"sessions/{options.session_id}/stop/"), options.json)

    def lookup(self, *args):
        options = self.commands["lookup"].parse_args(args)
        query = {key: value for key, value in vars(options).items() if key != "json" and value is not None}
        data = self.request("GET", "lookup/?" + urlencode(query))
        if options.json:
            print(json.dumps(data, indent=2))
            return
        for row in data["results"]:
            avg = row["metrics"].get("latency_ms", {}).get("avg")
            print(f"VM {row['source_vm']} -> VM {row['target_vm']}: RTT={avg} ms | age={row['age_sec']}s | usable={row['usable']}")
            if row["error_message"]:
                print(f"Failure: {row['error_message']}")
        if not data["results"]:
            print("No probe measurements.")

    def print_help(self):
        self.parser.print_help()
