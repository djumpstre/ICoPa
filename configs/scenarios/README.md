# Scenarios for ICoPa

Canonical scenario action contract:

- Use `spec.phaseTemplates` only.
- Each action object supports only:
  - `type`
  - `targetRef` (single-target actions)
  - `targetRefs` (multi-target actions)
  - `preset` (required only for `run_runtime_preset`)
  - `parameters`
- Legacy fields are rejected:
  - `execAction`
  - `action`
  - `steps`
  - `target`
  - `targets`
