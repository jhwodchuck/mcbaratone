# Security policy

## Reporting a vulnerability

Please use GitHub's private vulnerability-reporting or security-advisory flow
for this repository. Do not open a public issue containing an exploit, token,
private server address, or other sensitive operational detail.

## Bridge exposure

The Fabric bridge is a control surface and does not provide an authentication
layer. Bind or route it only over loopback or an explicitly trusted private
transport. Do not expose the bridge directly to the public internet.

## Safe testing

The default Pytest configuration runs offline tests only. Functional tests can
read or mutate a Minecraft world and must be selected individually according
to [`tests/functional/README.md`](tests/functional/README.md).

## Repository hygiene

Do not commit credentials, host inventories, world saves, checkpoints, logs,
telemetry captures, player data, personal information, or private network
details. Use placeholder values in examples.
