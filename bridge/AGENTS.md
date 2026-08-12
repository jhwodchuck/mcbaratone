# Fabric bridge instructions

Follow [`../AGENTS.md`](../AGENTS.md). This directory is in scope only when the
task explicitly targets the bridge.

Build with Java 25 and the tracked Gradle wrapper. The build requires
`libs/baritone-api-fabric-1.15.0-9-gd93f1582.jar`. Do not hardcode a local JDK
path. Keep matching Python route contracts and focused Java tests in scope.

A passing build is not deployment evidence. Do not copy a JAR, restart a game
JVM, or rotate clients without explicit live authorization. A deployed claim
requires the exact artifact hash, installation target, restart, loaded bridge
version, and a live capability check.
