"""Base classes for ICoPa CLI command groups."""

import json
import sys
import requests
from argcomplete.completers import BaseCompleter

from icopa_cli.icopa_config import IcopaConfig


class ICoPaBaseCompleter(BaseCompleter):
    """Base class for autocompletion."""

    def __init__(self, resource_url: str) -> None:
        super().__init__()
        self.url = resource_url

    def __call__(self, **kwargs):
        return self.get_data_for_completion()

    def cache_data(self, data):
        pass

    def get_data_for_completion(self) -> list:
        return NotImplementedError

    def call_api(self, url=None):
        config = IcopaConfig.get_current_config()

        if url is None:
            url = self.url

        try:
            resp = requests.request(
                "GET",
                f"{config['server'].rstrip('/')}/{url.lstrip('/')}",
                headers={
                    "Authorization": f"Bearer {config.get('token', '')}",
                },
                timeout=3,
            )
            resp.raise_for_status()
            data = resp.json()
            return True, data

        except requests.exceptions.HTTPError:
            err_type = resp.status_code

            if err_type == 400:
                print("[Bad Request '400'] Please check the request parameters.")

            if err_type == 401:
                print("[Unauthorized '401'] Login required. Cached token expired.")

            if err_type == 404:
                print("[Not Found '404'] Check the resource name and try again.")

            if err_type == 500:
                print("[Internal Server Error '500'] Please contact the administrator.")

            sys.exit(1)

        except requests.exceptions.RequestException:
            print("[ConnectionError] Can not connect to the API server.")
            sys.exit(1)

        except Exception as exc:
            print("[Unknown Error]", exc)
            sys.exit(1)




class CommandGroupBase:
    """Base class for command group."""

    COMMAND_LIST = []

    def __init__(self, subparsers, group_name, aliases=None) -> None:
        self.parser = subparsers.add_parser(group_name, aliases=aliases or [], help="ICoPa CLI")
        self.subparsers = self.parser.add_subparsers(
            dest="subcommand",
            help=f"{group_name} command: {self.COMMAND_LIST}",
        )

        self.commands = {}
        for command in self.COMMAND_LIST:
            self.commands.update({command: self.subparsers.add_parser(command)})

    def run(self, *args):
        if len(args) > 0:
            parsed_args = self.parser.parse_args(args)
            method_name = parsed_args.subcommand.replace("-", "_")
            getattr(self, method_name)(*args[1:])
        else:
            self.print_help()

    def call_api(
        self,
        method: str,
        url: str,
        data=None,
        json_data=None,
        files=None,
        headers=None,
        auth_token=None,
        expect_json=True,
        timeout=10,
    ):
        if headers is None:
            headers = {}
        if auth_token is None:
            auth_token = IcopaConfig.get_current_config().get("token")
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        try:
            resp = requests.request(
                method,
                url,
                data=data,
                json=json_data,
                files=files,
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            if expect_json:
                return True, resp.json()
            return True, resp.content

        except requests.exceptions.HTTPError:
            err_type = resp.status_code

            if err_type == 400:
                print("[Bad Request '400'] Please check the request parameters.")

            if err_type == 401:
                print("[Unauthorized '401'] Login required. Cached token expired.")
                print("Login again using: icopa config login")

            if err_type == 404:
                print("[Not Found '404'] Check the resource name and try again.")

            if err_type == 500:
                print("[Internal Server Error '500'] Please contact the administrator.")

            print(resp.text)
            sys.exit(1)

        except requests.exceptions.RequestException:
            print("[ConnectionError] Can not connect to the API server.")
            sys.exit(1)

        except Exception as exc:
            print("[Unknown Error]", exc)
            sys.exit(1)

    def print_help(self):
        return NotImplementedError

    @staticmethod
    def _summarize_text(value, *, verbose: bool, max_chars: int = 180) -> str:
        text = str(value or "")
        if verbose:
            return text
        if not text:
            return ""
        lines = text.splitlines()
        first_line = lines[0] if lines else text
        if len(first_line) > max_chars:
            first_line = f"{first_line[: max_chars - 3]}..."
        if len(lines) > 1:
            return f"{first_line} ... (+{len(lines) - 1} more lines, use -v for full content)"
        return first_line

    @classmethod
    def _normalize_value_for_display(cls, value, *, verbose: bool, depth: int = 0):
        if isinstance(value, str):
            return cls._summarize_text(value, verbose=verbose)
        if isinstance(value, dict):
            if not verbose and depth >= 3:
                return f"<object with {len(value)} keys; use -v for full value>"
            ordered_items = sorted(value.items(), key=lambda item: str(item[0]))
            if verbose:
                return {
                    str(key): cls._normalize_value_for_display(val, verbose=True, depth=depth + 1)
                    for key, val in ordered_items
                }
            max_items = 6
            rendered = {
                str(key): cls._normalize_value_for_display(val, verbose=False, depth=depth + 1)
                for key, val in ordered_items[:max_items]
            }
            if len(ordered_items) > max_items:
                rendered["..."] = f"+{len(ordered_items) - max_items} more keys (use -v)"
            return rendered
        if isinstance(value, list):
            if not verbose and depth >= 3:
                return f"<list with {len(value)} items; use -v for full value>"
            if verbose:
                return [cls._normalize_value_for_display(item, verbose=True, depth=depth + 1) for item in value]
            max_items = 4
            rendered = [
                cls._normalize_value_for_display(item, verbose=False, depth=depth + 1)
                for item in value[:max_items]
            ]
            if len(value) > max_items:
                rendered.append(f"... +{len(value) - max_items} more items (use -v)")
            return rendered
        return value

    @classmethod
    def _format_value_for_display(cls, value, *, verbose: bool) -> str:
        normalized = cls._normalize_value_for_display(value, verbose=verbose)
        if isinstance(normalized, (dict, list)):
            return json.dumps(
                normalized,
                ensure_ascii=True,
                sort_keys=True,
                indent=2 if verbose else None,
            )
        return str(normalized)

    @staticmethod
    def _print_labeled_value(label: str, value) -> None:
        lines = str(value).splitlines() or [""]
        print(f"{label}{lines[0]}")
        padding = " " * len(label)
        for line in lines[1:]:
            print(f"{padding}{line}")

    @classmethod
    def _print_updated_fields(cls, updated_items: list[dict], *, entity_label: str, verbose: bool) -> None:
        for item in updated_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "-")
            print(f"Updated {entity_label}: {name}")
            changed_fields = item.get("updated_fields")
            if not isinstance(changed_fields, dict) or not changed_fields:
                print("Changed Fields: (none)")
                continue
            print("Changed Fields:")
            for field_name in sorted(changed_fields.keys()):
                change = changed_fields.get(field_name) if isinstance(changed_fields, dict) else {}
                old_value = cls._format_value_for_display(
                    change.get("old") if isinstance(change, dict) else None,
                    verbose=verbose,
                )
                new_value = cls._format_value_for_display(
                    change.get("new") if isinstance(change, dict) else None,
                    verbose=verbose,
                )
                print(f"- {field_name}")
                cls._print_labeled_value("  old: ", old_value)
                cls._print_labeled_value("  new: ", new_value)
