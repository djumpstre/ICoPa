import argparse
import contextlib
import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase
from rest_framework_simplejwt.tokens import AccessToken

from icopa_cli.command_group.probe import ProbeCommandGroup
from inventory.models import VM


class ProbeCLIIntegrationTests(LiveServerTestCase):
    @patch("micro_probing.tasks.execute_probe_session.apply_async")
    def test_start_info_lookup_stop_over_http(self, dispatch):
        dispatch.return_value.id = "cli-test-task"
        user = get_user_model().objects.create_user(username="cli-probe-user")
        source = VM.objects.create(name="edge", address="192.0.2.1", created_by=user)
        target = VM.objects.create(name="cloud", address="192.0.2.2", created_by=user)
        group = ProbeCommandGroup(argparse.ArgumentParser().add_subparsers())
        config = {"server": self.live_server_url, "token": str(AccessToken.for_user(user))}
        output = io.StringIO()
        with patch("icopa_cli.icopa_config.IcopaConfig.get_current_config", return_value=config), contextlib.redirect_stdout(output):
            group.start(str(source.pk), str(target.pk), "--window-count", "1")
            from .models import ProbeSession
            session_id = str(ProbeSession.objects.get().pk)
            group.info(session_id)
            group.lookup()
            group.stop(session_id)
        text = output.getvalue()
        self.assertIn("PENDING", text)
        self.assertIn("STOPPED", text)
        self.assertIn("No probe measurements.", text)
        self.assertNotIn('"source_vm":', text)
