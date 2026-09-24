# Contributing

Start with [development setup and checks](docs/development.md). Keep changes
focused and explain the behavior changed, validation performed, and limitations
in each pull request. Include a minimal reproduction for bug reports.

Keep CLI, Hub, and Core responsibilities separate. Update related API contracts,
configuration examples, documentation, and tests in the same change. Use
synthetic fixtures and mocked remote/cloud calls for automated tests.

Never commit credentials, login caches, private inventories, or local databases.
Use [`.private/`](docs/private_data.md). Before sharing logs or data, remove
account identifiers and sensitive host details. Report suspected credential
exposure privately to the repository maintainer rather than posting the secret
in an issue or pull request.

For paper-related changes, document provenance and licensing of contributed
data, along with the configuration and commands needed to reproduce results.
