import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from .local_secret import resolve_secret_key


class LocalSecretTests(unittest.TestCase):
    @patch.dict(os.environ, {"DJANGO_SECRET_KEY": ""})
    def test_development_key_is_stable_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = resolve_secret_key(debug=True, directory=root)
            self.assertGreaterEqual(len(first), 50)
            self.assertEqual(first, resolve_secret_key(debug=True, directory=root))
            self.assertEqual((root / "django_secret_key").stat().st_mode & 0o777, 0o600)

    @patch.dict(os.environ, {"DJANGO_SECRET_KEY": ""})
    def test_deployment_requires_key(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_secret_key(debug=False, directory=Path("unused"))

    @patch.dict(os.environ, {"DJANGO_SECRET_KEY": "short"})
    def test_deployment_rejects_short_key(self):
        with self.assertRaises(ImproperlyConfigured):
            resolve_secret_key(debug=False, directory=Path("unused"))

    def test_configured_key_does_not_create_local_file(self):
        import secrets
        key = secrets.token_urlsafe(64)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DJANGO_SECRET_KEY": key}):
            self.assertEqual(resolve_secret_key(debug=False, directory=Path(directory)), key)
            self.assertFalse(list(Path(directory).iterdir()))
