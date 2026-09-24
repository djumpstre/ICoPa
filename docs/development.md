# Development

Use the repository's Python 3.13 development container. Run commands from
`/workspace`. On the host, prefix project commands with:
`docker exec icopa_devcontainer-icopa-devcon-1 sh -lc 'cd /workspace && ...'`.

## Setup

```sh
python -m pip install --upgrade 'pip>=26.2'
python -m pip install -r requirements-dev.txt
python -m pip install -e ./icopa_cli
mkdir -p -m 700 .private
test -f .private/hub.env || cp icopa_hub/.env.example .private/hub.env
chmod 600 .private/hub.env
set -a
. .private/hub.env
set +a
python icopa_hub/manage.py migrate
python icopa_hub/manage.py createsuperuser
```

The Hub uses local SQLite. The environment file must be sourced explicitly;
Django does not load it automatically. Database, signing key, and uploaded SSH
credentials live under ignored `.private/`. See [private data](private_data.md).

## Run

In the configured terminal:

```sh
export CELERY_BROKER_URL=redis://127.0.0.1:6379/0
redis-server --daemonize yes
python icopa_hub/manage.py runserver 0.0.0.0:8000
```

In another terminal, load `.private/hub.env`, export the same broker URL, and run:

```sh
python -m celery -A settings.celery worker --loglevel=info
```

For the GUI, run `npm ci` then `npm run dev -- --host 0.0.0.0` in `icopa_gui`.
Forward ports 8000 and 5173 to your local machine. Login with your personal Hub
account using `icopa config login --server http://127.0.0.1:8000`.

Remote experiments additionally need reachable inventory hosts, SSH credentials,
runtime images, and applicable cloud credentials. Provisioning can incur costs.
Public image fields are intentionally empty: follow the
[container-image contract](container_images.md) before uploading a runtime or
enabling a container preset. The devcontainer is the development environment,
not the image deployed to experiment hosts.

SSH and SCP trust new host keys on first connection and reject changed keys.
Verify host fingerprints before initial use and prepopulate the worker's
`known_hosts` for controlled environments. If a VM is recreated, verify its new
fingerprint before replacing its old entry.

Logout revokes refresh tokens; already issued access tokens remain valid until
their expiry (30 minutes by default). Users with older, untracked refresh tokens
must log in again. This does not replace credential rotation after exposure.

## Checks

With `.private/hub.env` loaded:

```sh
python icopa_hub/manage.py check
python icopa_hub/manage.py makemigrations --check --dry-run
python icopa_hub/manage.py test accounts inventory runtime_env scenarios cloud_provisioning profiling_exps micro_probing settings.test_local_secret
python -m pytest icopa_core/test icopa_cli/tests
```

Run `npm run lint`, `npm run test:run`, and `npm run build` inside `icopa_gui`.
Unit tests do not establish successful execution on physical VMs. Live trials
must record image versions, host configuration, and input/output artifacts.

## Boundaries

Keep the CLI as a Hub API client. Put reusable execution in Core, domain
invariants in models, and orchestration in services/tasks. Update API contracts,
configs, and tests together when changing a canonical name. New Hub behavior
needs API coverage. Keep fixtures independent of live credentials and cloud APIs.

See the [online probing API](online_probing.md) for latency sessions, scheduler
lookup, and monitoring export. Use the Hub in a trusted research environment;
the development setup is not hardened for Internet-facing deployment.
