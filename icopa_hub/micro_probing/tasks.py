"""One bounded task per session; duplicate deliveries cannot start a second loop."""

import time

from celery import shared_task
from django.utils import timezone

from icopa_core.connectors.vm_ssh_client import VMSSHClient
from icopa_core.profiling.probes.micro_latency import measure_latency
from .models import ProbeSession, ProbeWindow


def dispatch_session(session):
    try:
        task = execute_probe_session.apply_async(args=[session.pk], retry=False)
        ProbeSession.objects.filter(pk=session.pk).update(task_id=task.id)
        return True
    except Exception:
        session.finish(ProbeSession.Status.FAILED, "Could not queue probe session. Check the Celery broker.")
        return False


@shared_task(soft_time_limit=3600, time_limit=3630)
def execute_probe_session(session_id):
    claimed = ProbeSession.objects.filter(pk=session_id, status=ProbeSession.Status.PENDING).update(
        status=ProbeSession.Status.RUNNING, started_at=timezone.now(),
    )
    if not claimed:
        return
    session = ProbeSession.objects.get(pk=session_id)
    try:
        for sequence in range(1, session.window_count + 1):
            session = ProbeSession.objects.select_related("source_vm__credential", "target_vm").get(pk=session_id)
            if session.status != ProbeSession.Status.RUNNING:
                return
            source, target = session.source_vm, session.target_vm
            if source.archived or target.archived or source.created_by_id != session.created_by_id or target.created_by_id != session.created_by_id:
                raise ValueError("Probe inventory is no longer available to the session owner.")
            credential = source.credential
            if credential and credential.created_by_id != session.created_by_id:
                raise ValueError("Source SSH credential ownership changed.")
            key_path = (credential.key_file.path if credential.key_file else credential.key_path) if credential else None
            client = VMSSHClient(source.address, source.user_name, source.port, key_path)
            metrics, error = {}, ""
            try:
                metrics = measure_latency(client, target.address, session.duration_sec)
            except Exception as exc:
                error = str(exc)
            ProbeWindow.objects.create(
                session=session, sequence=sequence, source_address=source.address,
                target_address=target.address, metrics=metrics, error_message=error,
            )
            if error:
                session.finish(ProbeSession.Status.FAILED, error)
                return
            if sequence < session.window_count:
                for _ in range(session.interval_sec):
                    if not ProbeSession.objects.filter(pk=session_id, status=ProbeSession.Status.RUNNING).exists():
                        return
                    time.sleep(1)
        session.finish(ProbeSession.Status.SUCCEEDED)
    except Exception as exc:
        session.finish(ProbeSession.Status.FAILED, str(exc))
