# Architecture and evidence boundaries

## Components

### Python controller

The Python package owns planning, phase progression, survival gates,
checkpoint state, transport abstraction, and observed-effect verification.
Policies are separated from transport mechanics so the same controller logic
can be exercised with deterministic fakes.

### Fabric bridge

The Java bridge runs inside the Minecraft client. It validates and dispatches
commands, publishes events, correlates responses with sequence numbers, and
records uncertain mutations. TCP, WebSocket, and MCP-facing integrations share
contracts without pretending their delivery behavior is identical.

### Dashboard

The React frontend presents controller and bridge observations. It is an
operational view, not an independent source of truth.

### Test layers

Offline tests cover state machines, contracts, retries, reconciliation, and
safety gates. Functional harnesses require an explicitly selected disposable
or read-only world. A bridge build proves compilation; a functional response
proves only the behavior that was actually observed.

## Evidence ladder

| Level | Evidence | What it proves |
| --- | --- | --- |
| 1 | Controller dispatch | The controller attempted an action |
| 2 | Bridge response | The bridge accepted or rejected it |
| 3 | Completion signal | The requested operation reached a terminal state |
| 4 | World observation | The expected block, inventory, position, entity, or health change occurred |
| 5 | Durable state | The verified result survived checkpointing, recovery, and later progression |

Higher levels include the lower context but cannot be inferred from them. A
successful response is therefore never reported as a durable gameplay result
without the corresponding observation.

## Uncertain mutations

Read operations may be retried within a bound. Mutating operations require
idempotency, correlation, or post-timeout reconciliation before any replay.
The mutation ledger and observed-effect helpers make that decision explicit.

## Public/private split

The public repository contains reproducible source, generic fixtures, and
test contracts. Environment-specific deployment and live-world state remain
outside the source tree. Private operations pin the exact public commit they
deploy rather than maintaining a divergent copy of the core.
