"""Celery log formatter helpers for colored scenario/task tags."""

from __future__ import annotations

import logging
import os
import re
import sys

RESET = "\033[0m"
BOLD_CYAN = "\033[1;36m"
BOLD_YELLOW = "\033[1;33m"
BOLD_MAGENTA = "\033[1;35m"
DIM_WHITE = "\033[2;37m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
BOLD_RED = "\033[1;31m"
BLUE = "\033[0;34m"

SCENARIO_TAG_RE = re.compile(r"\[scenario-[^\]]+\]")
TASK_PHASE_TAG_RE = re.compile(r"\[task:phase-action\]")
TASK_TAG_RE = re.compile(r"\[task:(?!phase-action\])[^\]]+\]")
ID_TAG_RE = re.compile(r"\[id:[^\]]*\]")
DETAIL_PAIR_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)=([^ \]]+)")
LEVEL_RE = re.compile(r"(: )(DEBUG|INFO|WARNING|ERROR|CRITICAL|FATAL)(/)")

LEVEL_COLORS = {
    "DEBUG": BLUE,
    "INFO": GREEN,
    "WARNING": YELLOW,
    "ERROR": RED,
    "CRITICAL": BOLD_RED,
    "FATAL": BOLD_RED,
}


def _color(text: str, code: str) -> str:
    return f"{code}{text}{RESET}"


def _supports_color() -> bool:
    force_color = os.getenv("FORCE_COLOR", "").strip().lower()
    if force_color in {"1", "true", "yes", "on"}:
        return True
    if os.getenv("NO_COLOR"):
        return False
    return bool(getattr(sys.stderr, "isatty", None) and sys.stderr.isatty())


class CeleryScenarioColorFormatter(logging.Formatter):
    """Apply focused colors to scenario/task tags and detail key/value pairs."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        if not _supports_color():
            return rendered
        rendered = LEVEL_RE.sub(
            lambda m: f"{m.group(1)}{_color(m.group(2), LEVEL_COLORS.get(m.group(2), GREEN))}{m.group(3)}",
            rendered,
        )
        rendered = SCENARIO_TAG_RE.sub(
            lambda m: _color(m.group(0), BOLD_CYAN), rendered
        )
        rendered = TASK_TAG_RE.sub(lambda m: _color(m.group(0), BOLD_MAGENTA), rendered)
        rendered = TASK_PHASE_TAG_RE.sub(
            lambda m: _color(m.group(0), BOLD_YELLOW), rendered
        )
        rendered = ID_TAG_RE.sub(lambda m: _color(m.group(0), DIM_WHITE), rendered)
        rendered = DETAIL_PAIR_RE.sub(
            lambda m: f"{_color(m.group(1), DIM_WHITE)}={_color(m.group(2), GREEN)}",
            rendered,
        )
        return rendered


def install_color_formatters(logger: logging.Logger | None) -> None:
    """Swap stream handler formatters with the scenario-aware formatter."""
    if logger is None:
        return
    for handler in logger.handlers:
        existing = handler.formatter
        if isinstance(existing, CeleryScenarioColorFormatter):
            continue
        fmt = getattr(existing, "_fmt", None)
        datefmt = getattr(existing, "datefmt", None)
        handler.setFormatter(CeleryScenarioColorFormatter(fmt=fmt, datefmt=datefmt))
