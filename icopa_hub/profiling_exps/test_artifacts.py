from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .services.artifacts import delete_run_artifacts, resolve_run_artifact_dirs


class RunArtifactTests(SimpleTestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.enterContext(self.settings(BASE_DIR=self.base))
        self.relative = "static/profiling_exps/example/run_7"
        self.run = Mock(id=7, collected_metrics_path=f"{self.relative}/metrics", results=[])
        self.run.get_experiment_result_backend_path.return_value = f"{self.relative}/metrics"

    def test_delete_only_run_root_and_keep_siblings(self):
        root = self.base / self.relative
        (root / "metrics").mkdir(parents=True)
        sibling = root.parent / "run_8"
        sibling.mkdir()
        deleted, missing, errors = delete_run_artifacts(self.run)
        self.assertEqual(deleted, [str(root)])
        self.assertEqual((missing, errors), ([], []))
        self.assertFalse(root.exists())
        self.assertTrue(sibling.exists())

    def test_reject_untrusted_paths(self):
        invalid = [
            ".private/run_7/metrics",
            "static/profiling_exps/../../.private/run_7/metrics",
            str(self.base / self.relative),
            "static/profiling_exps/example/run_8/metrics",
            "static/profiling_exps/run_7/metrics",
        ]
        for path in invalid:
            with self.subTest(path=path):
                self.run.collected_metrics_path = path
                self.run.get_experiment_result_backend_path.return_value = path
                self.run.results = [{"metrics_backend_dir": path}, None]
                self.assertEqual(resolve_run_artifact_dirs(self.run), set())

    def test_never_follow_symlinked_run_or_ancestor(self):
        target = self.base / "private"
        target.mkdir()
        secret = target / "keep.txt"
        secret.write_text("fixture", encoding="utf-8")
        root = self.base / self.relative
        root.parent.mkdir(parents=True)
        root.symlink_to(target, target_is_directory=True)
        self.assertEqual(delete_run_artifacts(self.run), ([], [], []))
        self.assertTrue(secret.exists())
        root.unlink()
        root.parent.rmdir()
        root.parent.symlink_to(target, target_is_directory=True)
        self.assertEqual(resolve_run_artifact_dirs(self.run), set())

    def test_missing_and_failed_deletions_are_reported(self):
        root = self.base / self.relative
        self.assertEqual(delete_run_artifacts(self.run), ([], [str(root)], []))
        root.mkdir(parents=True)
        with patch("profiling_exps.services.artifacts.shutil.rmtree", side_effect=OSError("denied")):
            deleted, missing, errors = delete_run_artifacts(self.run)
        self.assertEqual((deleted, missing), ([], []))
        self.assertIn("denied", errors[0])
