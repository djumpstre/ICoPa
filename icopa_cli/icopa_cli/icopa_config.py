"""ICoPa config file utilities."""

import os
import sys
import yaml


class IcopaConfig:
    """Class to handle the ICoPa CLI config file."""

    @staticmethod
    def get_config_path() -> str:
        """
        Get the path of the config file
        Default path: ~/.icopa/config
        """
        config_path = os.environ.get("ICOPA_CONFIG", None)
        if config_path is None:
            config_path = "~/.icopa/config"

        config_path = os.path.expanduser(config_path)
        return config_path

    @staticmethod
    def create_config_file(config_path: str):
        """
        Create a new config file
        """
        if os.path.isfile(config_path):
            print("Config file already exists")
        else:
            # check if the config folder exists
            if not os.path.isdir(os.path.dirname(config_path)):
                os.makedirs(os.path.dirname(config_path), mode=0o700, exist_ok=True)

            # create the config file
            descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                yaml.safe_dump({
                    'user': '',
                    'server': '',
                    'token': ''
                },
                    file,
                    default_flow_style=False)
            print(f'Create config file in path: {config_path}')

    @classmethod
    def load_kuberos_config(cls) -> dict:
        """Load the cached authentication token and api server address."""
        config_path = cls.get_config_path()

        if os.path.isfile(config_path):
            with open(config_path, "r", encoding="utf-8") as file:
                config = yaml.safe_load(file)
        else:
            cls.create_config_file(config_path)
            config = {}

        if config is None:
            config = {}
        config.setdefault('user', '')
        config.pop('password', None)
        config.setdefault('server', '')
        config.setdefault('token', '')
        config.setdefault('login_time', '')
        return config

    @classmethod
    def load_icopa_config(cls) -> dict:
        """Alias for loading ICoPa CLI config."""
        return cls.load_kuberos_config()

    @classmethod
    def update_config(cls, config: dict):
        """Update the local cli config file."""
        config_path = cls.get_config_path()
        current = cls.load_kuberos_config()
        current.update(config)
        current.pop('password', None)

        os.chmod(config_path, 0o600)
        with open(config_path, "w", encoding="utf-8") as file:
            yaml.safe_dump(current, file, default_flow_style=False)

    @classmethod
    def get_current_config(cls) -> dict:
        """Get config for CLI usage (flat format)."""
        return cls.load_kuberos_config()
