# Survival progression acceptance suite

Suite 1200 is the no-cheat acceptance path for the autonomous bot. It observes
the live bridge and `spawn_to_dragon_checkpoint.json`; it does not teleport,
give items, change game rules, place blocks, break blocks, or start Baritone.
It still opens a bridge connection, so prefer running it when the mutating
controller is idle unless concurrent read-only clients have been verified.

The gates are deliberately cumulative:

| Test | Acceptance milestone |
| --- | --- |
| T1200 | Healthy live runtime |
| T1201 | Durable bridge and spawn bootstrap |
| T1202 | Initial wood, cobblestone, and stone tool |
| T1203 | In-world verified starter house |
| T1204 | Renewable food and essential iron equipment |
| T1205 | Diamond pickaxe and level-30 enchanting |
| T1206 | In-world verified Nether portal |
| T1207 | Fortress location and six blaze rods |
| T1208 | Twelve Eyes of Ender |
| T1209 | Persisted stronghold coordinates |
| T1210 | Persisted End portal and End entry |
| T1211 | Explicit durable dragon-defeat evidence |
| T1212 | Elytra and five shulker boxes |
| T1213 | Megabase and terraforming handoff |

## Running it

List the suite without connecting to Minecraft:

```powershell
C:\gh\.venvs\mcbaratone\Scripts\python.exe tests\functional\run_tests.py --list
```

Run one read-only gate against the production checkpoint:

```powershell
C:\gh\.venvs\mcbaratone\Scripts\python.exe tests\functional\run_tests.py `
  --test T1202 `
  --checkpoint C:\gh\mcbaratone\spawn_to_dragon_checkpoint.json
```

Run several gates after the Minecraft driver is idle:

```powershell
C:\gh\.venvs\mcbaratone\Scripts\python.exe tests\functional\run_tests.py `
  --test T1200,T1201,T1202,T1203 `
  --checkpoint C:\gh\mcbaratone\spawn_to_dragon_checkpoint.json
```

Do not use the unfiltered `run_tests.py` command in the real autonomous world.
Suites 100-1000 contain arena setup, admin commands, and destructive teardown.

## Promotion rule

Each production capability should be implemented under `src/baritone_client`,
then exercised by focused unit tests and verified by its Suite 1200 gate. Do
not keep a separate working implementation inside a functional test. Persist
locations and milestone evidence in the production `StateManager` checkpoint
so a failure or restart can be resumed and independently verified.
