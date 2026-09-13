# Functional test guide

Functional tests connect to a running Minecraft bridge. They are not all safe
for the same world.

## Suite safety map

| Suites | Purpose | World impact |
| --- | --- | --- |
| 100-900 | Controlled action and integration arenas | Admin/destructive: teleport, give, set blocks, clear regions, and teardown |
| 1000 | Larger base and integration prototypes | Hybrid; extensive admin setup and supplied materials |
| 1100 | Persistent farming, house, storage, and tower prototype | Survival-oriented but mutates the live world |
| 1200 | Spawn-to-endgame acceptance evidence | Read-only bridge and checkpoint observations |

Never run all functional suites in a real autonomous world. Use `--list`, then
select an explicit test ID or suite.

The archived action inventory and Suites 100-900 narrative are planning aids,
not execution instructions. The table above and the runner's `--list` output
are the current safety/coverage authority. Preserve these acceptance classes
when adding a test:

| Acceptance class | Required observable |
| --- | --- |
| Movement/navigation | Position, progress, collision, or bounded failure |
| Inventory/crafting | Slot/count delta plus expected output |
| World mutation | Exact block/entity postcondition and bounded cleanup |
| Combat | Health/hostile/outcome telemetry, not command success alone |
| Recovery | Checkpoint, identity, process state, and safe resume result |

## Disposable worlds

Mutating suites require a disposable world that is isolated from any valued or
autonomous world. The runner requires the expected server identity for every
mutating selection:

```powershell
python tests\functional\run_tests.py `
  --test T600 `
  --host localhost `
  --port 5695 `
  --expect-server localhost:25580
```

Provisioning and lifecycle automation is environment-specific and is not part
of this public source repository.

## Layout

```text
run_tests.py                    CLI and suite registration
test_base.py                    TestCase, TestSuite, and bridge harness
shared/                         Reusable live-world operations
survival/                       Read-only Suite 1200 implementation and docs
extended_suite_100.py ...       Legacy compatibility modules
extended_suite_1100.py          Persistent mutating prototype
```

Suite 1200 was moved into the named `survival` package so its safety model and
acceptance contracts are discoverable. `extended_suite_1200.py` and
`survival_progression.py` remain as small compatibility imports.

## Commands

List tests without connecting:

```powershell
.\.venv\Scripts\python.exe tests\functional\run_tests.py --list
```

Run a read-only gate when the mutating controller is idle:

```powershell
.\.venv\Scripts\python.exe tests\functional\run_tests.py `
  --test T1202 `
  --checkpoint .\spawn_to_dragon_checkpoint.json
```

Run offline harness and contract tests without Minecraft:

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_functional_harness.py `
  tests\test_survival_progression.py
```

The repository-wide offline collection count changes frequently and exercises
slow retry paths. Check it with `python -m pytest --collect-only -q`; it is
separate from the manual live functional runner.

See [Suite 1200](survival/README.md) for the complete gate list and promotion
rule.

Historical live-world planning and operational snapshots are intentionally not
part of this public source repository.
