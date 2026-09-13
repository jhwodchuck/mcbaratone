# Contributing

Contributions that improve deterministic behavior, safety, observability,
transport contracts, documentation, or offline test coverage are welcome.

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

For dashboard changes:

```powershell
Set-Location dashboard
npm ci
npm run lint
npm run build
```

For bridge changes, use Java 25:

```powershell
Set-Location bridge
.\gradlew.bat --no-daemon --console=plain build
```

## Safety rules

- Do not run live or mutating tests without authorization for the exact world.
- Never point an unfiltered functional suite at a valued world.
- Treat command acceptance and bridge responses as intermediate evidence, not
  proof of a world change.
- Do not commit server addresses, checkpoints, telemetry, logs, world data, or
  personal information.
- Add focused offline tests for behavior changes and verify postconditions.
