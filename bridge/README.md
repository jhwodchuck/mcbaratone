# Baritone API Bridge

This mod provides the network bridge required for the Python client to control Baritone.

## Prerequisites

1.  **JDK 21**: Ensure you have Java 21 installed (`java -version`).
2.  **Fabric API**: This mod requires Fabric API.
3.  **Baritone API Jar**: You must copy your local `baritone-api-fabric-*.jar` into the `libs/` folder in this directory.
    *   **Important**: The file must be named `baritone-api-fabric-1.15.0.jar` to match the build configuration.
    *   Example: Copy `C:\Minecraft\MultiMC\instances\1.21.8\.minecraft\mods\baritone-api-fabric-1.15.0.jar` into `libs/` and rename if necessary.
    *   **Note**: If you don't copy this file, the build will fail.

## Building

Open a terminal in this directory (`bridge/`) and run:

### Windows
```powershell
./gradlew build
```
*(If you don't have gradle wrapper generated, you might need to install Gradle separately or run `gradle build`)*

### Linux/Mac
```bash
./gradlew build
```

## Installation

1.  After building, find the jar in `build/libs/`.
2.  Copy `baritone-api-bridge-1.0.0.jar` to your Minecraft `mods` folder.
3.  Start Minecraft. The bridge should listen on port **5555**.
