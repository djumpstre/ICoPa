from __future__ import annotations

from pathlib import Path

from icopa_core.connectors.vm_ssh_client import VMSSHResult
from icopa_core.task_executor.vm_scp_collect_metrics import (
    SCPCollectTarget,
    VMScpCollectMetricsExec,
    collect_metrics_from_vms,
)


def test_vm_scp_collect_metrics_exec_success(monkeypatch, tmp_path: Path) -> None:
    class _FakeSCPClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def scp_pull_directory(self, *, remote_dir: str, local_dir: str, timeout: int = 45) -> VMSSHResult:
            local_target_dir = Path(local_dir)
            local_target_dir.mkdir(parents=True, exist_ok=True)
            (local_target_dir / "rrt_summary.json").write_text("{}", encoding="utf-8")
            return VMSSHResult(success=True, returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(
        "icopa_core.task_executor.vm_scp_collect_metrics.build_vm_ssh_client",
        lambda *_args, **_kwargs: _FakeSCPClient(),
    )

    result = VMScpCollectMetricsExec(
        targets=[
            SCPCollectTarget(
                node_name="vm_home",
                host="127.0.0.1",
                username="ubuntu",
                port=22,
                key_path=__file__,
            )
        ],
        remote_dir="/tmp/netanalyzer/validate-20260218-010101",
        local_base_dir=str(tmp_path / "metrics"),
    ).execute()

    assert result["ok"] is True
    assert "vm_home" in result["targets"]
    assert result["targets"]["vm_home"]["ok"] is True
    assert "rrt_summary.json" in result["targets"]["vm_home"]["files"]


def test_collect_metrics_from_vms_reports_scp_errors(monkeypatch, tmp_path: Path) -> None:
    class _FakeSCPClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def scp_pull_directory(self, *, remote_dir: str, local_dir: str, timeout: int = 45) -> VMSSHResult:
            return VMSSHResult(success=False, returncode=1, stdout="", stderr="No such file")

    monkeypatch.setattr(
        "icopa_core.task_executor.vm_scp_collect_metrics.build_vm_ssh_client",
        lambda *_args, **_kwargs: _FakeSCPClient(),
    )

    result = collect_metrics_from_vms(
        targets=[
            SCPCollectTarget(
                node_name="cloud_vm_bw",
                host="127.0.0.2",
                username="ubuntu",
                port=22,
                key_path=__file__,
            )
        ],
        remote_dir="/tmp/netanalyzer/validate-20260218-020202",
        local_base_dir=str(tmp_path / "metrics"),
    )

    assert result["ok"] is False
    assert result["errors"]
    assert result["targets"]["cloud_vm_bw"]["ok"] is False
