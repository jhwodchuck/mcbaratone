# Repository guidance

This is the public source repository for `mcbaratone`. Keep public source and
private runtime operations separate.

## Working agreement

1. Inspect status before editing and preserve unrelated work.
2. Keep behavior changes focused and cover them with offline tests.
3. Run `python -m pytest -q` before handoff.
4. Build `bridge/` with Java 25 for bridge-scoped changes.
5. Run `npm run lint` and `npm run build` in `dashboard/` for UI changes.

## Live-world boundary

Repository work does not authorize Minecraft mutation. Do not start or restart
controllers, install bridge artifacts, issue admin commands, or run live tests
without explicit authorization for the exact world and driver.

Functional tests are classified in `tests/functional/README.md`. Listing tests
is offline; executing them connects to Minecraft. Never run an unfiltered
selection against a valued world.

## Publication boundary

Do not add server inventories, addresses, current handoffs, world maps,
checkpoints, telemetry captures, player data, logs, family configuration, or
recovery state. Examples must use placeholders and public-safe fixtures.
