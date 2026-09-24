# ICoPa GUI

React and TypeScript interface for the ICoPa Hub, using Material UI and Zustand.
It provides inventory, cloud VM, runtime, scenario, experiment, run, and
comparison views.

## Setup

Start the Hub using the [development guide](../docs/development.md). Run these
commands inside the development container from `/workspace/icopa_gui`:

```sh
npm ci
npm run dev -- --host 0.0.0.0 --port 5173
```

Forward ports 5173 and 8000. The default API URL is
`http://127.0.0.1:8000`; set `VITE_ICOPA_API_BASE_URL` in a local `.env` to
override it. Do not put secrets in `VITE_*` variables.

## Checks

```sh
npm run test:run
npm run lint
npm run build
```

## Views

- `/login`
- `/cloud`, `/cloud/:vmId`
- `/inventory`, `/inventory/:vmName`
- `/runtime`, `/runtime/:envName`
- `/scenario`, `/scenario/:scenarioName`
- `/experiment`, `/experiment/:expName`
- `/experiment/runs`, `/experiment/runs/:runId`
- `/experiment/comparison`
- `/experiment/comparision_store`, `/experiment/comparision_store/:comparisionId`

Use the CLI/API for configuration uploads and operations not exposed in the GUI.
Metric downloads are restricted to the configured Hub origin.
