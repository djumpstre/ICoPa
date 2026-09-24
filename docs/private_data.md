# Private Local Data

Use the repository-root `.private/` directory for local credentials and account
data. It is excluded from Git, Docker build contexts, and publication snapshots.
Only sanitized examples belong in tracked configuration folders.

```text
.private/
  hub.env                     # Local shell environment
  cli.config                  # Local CLI login cache (ICOPA_CONFIG)
  credentials/                # Cloud credentials and original SSH keys
  configs/                    # Private inventories, runtimes, and catalogs
  dev_db/                     # SQLite database and Django signing key
  uploads/inventory/ssh_keys/  # Hub-uploaded SSH keys
  backups/                    # Private backups
```

Follow the [development guide](development.md) to create and load `hub.env`.
The environment example sets `ICOPA_CONFIG=/workspace/.private/cli.config`.
Without that variable, the standalone CLI uses `~/.icopa/config`.

Copy public runtime templates into `.private/configs/` and provide your own
[container-image references](container_images.md). Load any environment-based
catalog overrides in both Hub and worker terminals, then restart the processes.

Uploaded SSH keys use private filesystem storage with mode 0600 and no public
URL. The API's `key_file` field is a storage-relative name, not a download link.
Experiment artifacts remain in ignored `icopa_hub/static/`; review and anonymize
selected research data before publishing it.

Do not serve the whole workspace or `.private/` over HTTP. Git ignore rules are
not encryption or a replacement for host access controls and secure backups.
Removing a credential from a file does not remove it from earlier Git commits
or revoke it. Rotate exposed credentials separately.

Do not place real cloud credentials alongside source files.
