"""Delete run artifacts only within the Hub's profiling results directory."""

from pathlib import Path, PurePosixPath
import shutil

from django.conf import settings

from ..models import ProfilingRun


def resolve_run_artifact_dirs(run: ProfilingRun) -> set[Path]:
    base = Path(settings.BASE_DIR).resolve()
    prefix = ("static", "profiling_exps")
    paths = [run.collected_metrics_path, run.get_experiment_result_backend_path()]
    if isinstance(run.results, list):
        paths.extend(
            item.get("metrics_backend_dir", "")
            for item in run.results if isinstance(item, dict)
        )

    resolved = set()
    run_segment = f"run_{run.id}"
    for raw_path in paths:
        path = PurePosixPath(str(raw_path or "").replace("\\", "/"))
        parts = path.parts
        if path.is_absolute() or ".." in parts or parts[:2] != prefix:
            continue
        if run_segment not in parts[3:]:
            continue
        run_parts = parts[:parts.index(run_segment, 3) + 1]
        candidate = base.joinpath(*run_parts)
        # Never follow a linked result directory into another run or private data.
        if any(base.joinpath(*run_parts[:index]).is_symlink() for index in range(1, len(run_parts) + 1)):
            continue
        if candidate.resolve().is_relative_to(base.joinpath(*prefix)):
            resolved.add(candidate)
    return resolved


def delete_run_artifacts(run: ProfilingRun) -> tuple[list[str], list[str], list[str]]:
    deleted, missing, errors = [], [], []
    for directory in sorted(resolve_run_artifact_dirs(run)):
        if not directory.exists():
            missing.append(str(directory))
            continue
        try:
            shutil.rmtree(directory)
            deleted.append(str(directory))
        except OSError as exc:
            errors.append(f"{directory}: {exc}")
    return deleted, missing, errors
