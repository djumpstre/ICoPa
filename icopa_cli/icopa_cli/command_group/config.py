"""Command group for ICoPa CLI config."""

import sys
import getpass
import requests
from datetime import datetime, timezone
from tabulate import tabulate

from argcomplete.completers import BaseCompleter

from icopa_cli.endpoints import Endpoints
from icopa_cli.command_group.base import CommandGroupBase
from icopa_cli.icopa_config import IcopaConfig


CONFIG_HELP = '''
ICoPa CLI [config] command group

Usage:
    icopa config <command> [-args]

Commands:
    login        Login to the ICoPa Hub API server
    logout       Logout from the ICoPa Hub API server
    list         Show current config values
    status       Show current login status
'''


class ContextNameCompleter(BaseCompleter):
    def __call__(self, **kwargs):
        return []


class ConfigCommandGroup(CommandGroupBase):
    """Command group [config] for ICoPa CLI."""

    COMMAND_LIST = ['login', 'logout', 'list', 'status']

    def __init__(self, subparsers) -> None:
        super().__init__(subparsers, 'config')

        self.init_subcommand_login()

    def init_subcommand_login(self):
        """
        Initialize the subcommand login
        """
        parser = self.commands['login']
        parser.add_argument(
            '-s', '--server',
            required=False,
            help="ICoPa Hub API server address (override config)")
        parser.add_argument(
            '-u', '--user',
            required=False,
            help="ICoPa username (override config)")

    def list(self):
        """
        List the contexts
        """
        config = IcopaConfig.load_icopa_config()
        data_to_display = [{
            'Server': config.get('server', ''),
            'User': config.get('user', ''),
            'Token': 'set' if config.get('token') else 'empty',
            'Login Time': config.get('login_time', ''),
        }]
        table = tabulate(data_to_display, headers="keys", tablefmt='plain')
        print(table)

    def login(self, *args):
        """
        Login to the api server
        """
        parser = self.commands['login']
        args = parser.parse_args(args)
        config_context = IcopaConfig.get_current_config()
        if args.server:
            config_context['server'] = args.server
        if args.user:
            config_context['user'] = args.user
        print("Config file:", IcopaConfig.get_config_path())
        print(f"Current Server: {config_context['server']}")
        print(f"Current User: {config_context['user']}")
        print("Login to ICoPa Hub: >>>")
        try:
            username = input("Username: ")
            password = getpass.getpass("Password: ")
        except KeyboardInterrupt:
            print("\nLogin cancelled.")
            return

        success, res = self._call_auth_api(
            'POST',
            f"{config_context['server'].rstrip('/')}/{Endpoints.LOGIN}",
            json_data={
                'username': username,
                'password': password
            }
        )
        if not success:
            print("Login failed")
            sys.exit(1)

        # update the config file
        config_context['token'] = res['access']
        config_context['user'] = username
        config_context['login_time'] = datetime.now(timezone.utc).isoformat()
        IcopaConfig.update_config(config_context)
        print("Login successfully")

    def status(self):
        """
        Print current login status
        """
        config = IcopaConfig.load_icopa_config()
        token_state = 'set' if config.get('token') else 'empty'
        print(f"Server: {config.get('server', '')}")
        print(f"User: {config.get('user', '')}")
        print(f"Token: {token_state}")
        print(f"Login Time: {config.get('login_time', '')}")

    def logout(self):
        """
        Logout from the api server
        """
        config_context = IcopaConfig.get_current_config()
        success, _ = self._call_auth_api(
            'POST',
            f"{config_context['server'].rstrip('/')}/{Endpoints.LOGOUT}",
            auth_token=config_context['token']
        )
        if success:
            print("Logout successfully")

    def _call_auth_api(self,
                       method: str,
                       url: str,
                       json_data: dict = None,
                       auth_token: str = None):
        """
        Modified version of call_api in command_group/base.py
        For status code handling in login and logout process
        """
        headers = {}
        if auth_token is not None:
            headers['Authorization'] = 'Bearer ' + auth_token

        try:
            resp = requests.request(method,
                                    url,
                                    json=json_data,
                                    headers=headers,
                                    timeout=2)

            status_code = resp.status_code

            if status_code == 400:
                print("Username or password is incorrect")
                return False, None

            elif status_code == 401:
                print("Unauthorized / Already logged out")
                return True, None

            elif status_code == 204:
                return True, None

            elif status_code == 500:
                print(
                    "[Internal Server Error '500'] Please contact the administrator.")
            else:
                data = resp.json()
                return True, data
            sys.exit(0)

        except Exception as exc:
            # Catch unknown error
            print("[Unknown Error]", exc)
            sys.exit(1)

    def print_help(self):
        """
        Print the help text
        """
        print(CONFIG_HELP)
