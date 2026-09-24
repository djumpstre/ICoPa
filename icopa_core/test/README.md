# Core Tests

Run `python -m pytest icopa_core/test` from the repository root in the
development container. These tests use local fixtures and mocked remote
operations; no cloud or SSH credentials are required.

See [development setup](../../docs/development.md) for dependencies and the
Hub and CLI checks. Passing unit tests does not validate live infrastructure.
