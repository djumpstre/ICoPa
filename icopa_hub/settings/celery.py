import os

from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger

from .celery_color_formatter import install_color_formatters

# os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings.development")

app = Celery("icopa")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@after_setup_logger.connect
def setup_worker_logger(logger, **kwargs):
    install_color_formatters(logger)


@after_setup_task_logger.connect
def setup_task_logger(logger, **kwargs):
    install_color_formatters(logger)
