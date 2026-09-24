#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK

# Copyright 2025 xxx
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import sys
import argparse
import argcomplete

from icopa_cli.command_group.config import ConfigCommandGroup
from icopa_cli.command_group.inventory import InventoryCommandGroup
from icopa_cli.command_group.runtime import RuntimeCommandGroup
from icopa_cli.command_group.scenario import ScenarioCommandGroup
from icopa_cli.command_group.experiment import ExperimentCommandGroup
from icopa_cli.command_group.cloud import CloudCommandGroup
from icopa_cli.command_group.probe import ProbeCommandGroup


CLI_HELP_SUMMARY = '''
ICoPa Command Line Tool

Usage:
    icopa <command_group> <command> [name] [-args]

    Call icopa <command_group> -h for more detailed usages.

Command Groups:

    config        Login/logout and view config
    inv           Inventory management for VMs, Kubernetes Cluster
    runtime       Runtime environment profile management
    scenario|sc   Scenario profile management
    exp           Profiling experiment plan management
    cloud         Cloud VM provisioning management
    probe         Online latency sessions and scheduler lookup

'''


class ICoPaCli:
    """Command line tool for ICoPa."""

    def __init__(self) -> None:
        self.parser = argparse.ArgumentParser(
            description="ICoPa Command Line Tool")

        group_subparsers = self.parser.add_subparsers(
            dest='group',
            help='Command group to execute'
        )

        # Add subparsers for each command group
        self.groups = {}
        scenario_group = ScenarioCommandGroup(subparsers=group_subparsers)
        self.groups.update({
            'config': ConfigCommandGroup(subparsers=group_subparsers),
            'inv': InventoryCommandGroup(subparsers=group_subparsers),
            'runtime': RuntimeCommandGroup(subparsers=group_subparsers),
            'scenario': scenario_group,
            'sc': scenario_group,
            'exp': ExperimentCommandGroup(subparsers=group_subparsers),
            'cloud': CloudCommandGroup(subparsers=group_subparsers),
            'probe': ProbeCommandGroup(subparsers=group_subparsers),
        })

        argcomplete.autocomplete(self.parser)
        args = self.parser.parse_args(sys.argv[1:2])

        # dispatch to the corresponding command group
        if not args.group in self.groups.keys():
            self.print_help()
            sys.exit(1)
        else:
            self.groups[args.group].run(*sys.argv[2:])

    def print_help(self):
        """
        Print the help message
        """
        print(CLI_HELP_SUMMARY)


def main():
    ICoPaCli()


if __name__ == "__main__":
    main()
