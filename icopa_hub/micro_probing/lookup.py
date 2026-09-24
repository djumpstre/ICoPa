"""Read-only scheduler views; online ICMP and offline workload results stay distinct."""

from django.db.models import OuterRef, Subquery
from django.utils import timezone

from .models import ProbeSession, ProbeWindow


def latest_measurements(user, *, max_age_sec=60, limit=100, source_vm=None, target_vm=None):
    windows = ProbeWindow.objects.filter(session__created_by=user)
    if source_vm is not None:
        windows = windows.filter(session__source_vm_id=source_vm)
    if target_vm is not None:
        windows = windows.filter(session__target_vm_id=target_vm)
    latest = ProbeWindow.objects.filter(
        session__created_by=user,
        session__source_vm_id=OuterRef("session__source_vm_id"),
        session__target_vm_id=OuterRef("session__target_vm_id"),
    ).order_by("-measured_at", "-pk")
    windows = windows.filter(pk=Subquery(latest.values("pk")[:1])).select_related(
        "session__source_vm", "session__target_vm",
    ).order_by("-measured_at", "-pk")[:limit]
    now = timezone.now()
    rows = []
    for window in windows:
        session = window.session
        age = max(0, (now - window.measured_at).total_seconds())
        fresh = age <= max_age_sec
        inventory_matches = (
            not session.source_vm.archived and not session.target_vm.archived
            and session.source_vm.created_by_id == user.pk and session.target_vm.created_by_id == user.pk
            and window.source_address == session.source_vm.address
            and window.target_address == session.target_vm.address
        )
        usable = bool(
            fresh and inventory_matches and not window.error_message
            and window.metrics.get("reachable")
            and session.status in [ProbeSession.Status.RUNNING, ProbeSession.Status.SUCCEEDED]
        )
        rows.append({
            "session_id": session.pk, "window_id": window.pk,
            "source_vm": session.source_vm_id, "target_vm": session.target_vm_id,
            "probe_type": "icmp", "status": session.status,
            "measured_at": window.measured_at.isoformat(), "age_sec": round(age, 3),
            "fresh": fresh, "inventory_matches": inventory_matches, "usable": usable,
            "metrics": window.metrics, "error_message": window.error_message,
        })
    return rows
