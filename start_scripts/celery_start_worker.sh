#!/usr/bin/env sh
set -eu

export CELERY_BROKER_URL="redis://host.docker.internal:6379/0"
export CELERY_RESULT_BACKEND="redis://host.docker.internal:6379/0"
export DJANGO_SETTINGS_MODULE="settings.settings"
export PYTHONPATH="/workspace${PYTHONPATH:+:${PYTHONPATH}}"

WORKER_NAME="${CELERY_WORKER_NAME:-icopa-$(hostname)-$$}"
QUEUE_NAME="${CELERY_QUEUE:-celery}"

cd /workspace/icopa_hub
echo "[celery-start] broker=${CELERY_BROKER_URL}"
echo "[celery-start] backend=${CELERY_RESULT_BACKEND}"
echo "[celery-start] worker_name=${WORKER_NAME} queue=${QUEUE_NAME}"
echo "[celery-start] pythonpath=${PYTHONPATH}"
celery -A settings worker -l info -n "${WORKER_NAME}" -Q "${QUEUE_NAME}"
