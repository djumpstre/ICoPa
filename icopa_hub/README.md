# ICoPa Hub

Django APIs and Celery orchestration for inventories, runtime environments,
scenarios, profiling experiments, cloud VMs, and online latency probes.

Follow the [setup guide](../docs/development.md) to configure the local SQLite
database, create a user, and run the Hub and worker.

Use the CLI's `icopa config login` command or `POST /auth/user_login/` to obtain
JWT access and refresh tokens. Protected API requests use
`Authorization: Bearer <access-token>`.

See the [online probing API](../docs/online_probing.md) for session, lookup, and
monitoring endpoints. This is a trusted research environment, not a hardened
multi-tenant service.
