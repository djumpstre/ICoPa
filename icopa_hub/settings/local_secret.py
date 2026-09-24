"""Keep development signing keys local; require an explicit key for deployment."""

import os
from pathlib import Path
import secrets
import tempfile

from django.core.exceptions import ImproperlyConfigured


def resolve_secret_key(*, debug: bool, directory: Path) -> str:
    configured = os.environ.get("DJANGO_SECRET_KEY", "").strip()
    if configured:
        if not debug and len(configured) < 50:
            raise ImproperlyConfigured("DJANGO_SECRET_KEY must have at least 50 characters outside development.")
        return configured
    if not debug:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY when DJANGO_DEBUG=0.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path = directory / "django_secret_key"
    if not key_path.exists():
        # Publish a complete file atomically so Hub and workers share one key.
        with tempfile.NamedTemporaryFile(dir=directory, mode="w") as temporary:
            temporary.write(secrets.token_urlsafe(64))
            temporary.flush()
            try:
                os.link(temporary.name, key_path)
            except FileExistsError:
                pass
    return key_path.read_text().strip()
