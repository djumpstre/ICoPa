# ICoPa CLI - Terminal SDK for ICoPa Platform

The ICoPa CLI provides a terminal-based interface to interact with the ICoPa Hub API.

## Installation
```bash
cd /workspace/icopa_cli
pip install -e .
```

## Manual config file

Create a config file at `~/.icopa/config` (or point `ICOPA_CONFIG` to a custom path). Example:

```yaml
user: ""
server: http://127.0.0.1:8000/
token: ""
```

Create your own Hub account. Do not commit populated configuration files or tokens.

## Quick start

1) Login

```bash
icopa config login --server http://127.0.0.1:8000
```

2) Inspect available inventory commands

```bash
icopa inv --help
```
