# Gradle wrapper provenance

`gradle-wrapper.jar` is the official Gradle 9.5.1 wrapper JAR (Apache-2.0). Its
SHA-256 is
`497c8c2a7e5031f6aa847f88104aa80a93532ec32ee17bdb8d1d2f67a194a9c7`.
The matching distribution URL and official distribution checksum are pinned
in `gradle-wrapper.properties`.
The Gradle distribution's complete license and bundled notices are tracked as
[`LICENSE-Gradle.txt`](LICENSE-Gradle.txt); preserve them with redistributed
wrapper artifacts.

When upgrading Gradle, regenerate the wrapper from an official distribution,
update both hashes and this note, run `scripts/check_repository_hygiene.py`,
and complete the Java 25 bridge build.
