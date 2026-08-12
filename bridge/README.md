# Baritone API Bridge

The Baritone API Bridge is a Fabric mod that provides a TCP-based network interface for controlling Minecraft's Baritone pathfinding library through external Python clients. It enables remote automation of Minecraft gameplay through a JSON-RPC protocol.

## Overview

The bridge serves as an intermediary between Python automation scripts and the Baritone pathfinding system, exposing Baritone's capabilities through a network API. It handles command dispatch, event publishing, schematic uploads, and mission state management.

## Architecture

The bridge consists of several key components working together:

- **BaritoneAPIBridge**: Main entry point and TCP server implementation
- **CommandDispatcher**: Routes commands to appropriate handlers
- **Command Handlers**: Process specific command types (movement, inventory, building, etc.)
- **UploadManager**: Handles schematic file uploads with chunking and validation
- **EventManager**: Buffers and publishes game events to connected clients
- **MissionController**: Manages high-level automation workflows and state

## Components

### Main Bridge (BaritoneAPIBridge)

The main bridge class implements the Fabric mod initializer and TCP server functionality.

**Key Features:**
- TCP server listening on port 5555
- Connection management with rate limiting and timeout handling
- Event listener registration for Minecraft events
- Command processing pipeline with JSON-RPC protocol
- Mission state integration

**TCP Server:**
- Accepts multiple concurrent connections (max 10)
- Uses thread pool for request handling
- Implements socket timeouts (30 seconds) and rate limiting (500 requests/10 seconds)
- Handles client disconnection cleanup

**Configuration Constants:**
```java
DEFAULT_PORT = 5555
MAX_CONNECTIONS = 10
SOCKET_TIMEOUT_MS = 30000
UPLOAD_TIMEOUT_MS = 300000  // 5 minutes for uploads
RATE_LIMIT_REQUESTS = 500   // per 10 second window
```

### Command System

The command system uses a handler pattern for extensibility and modularity.

#### CommandDispatcher [`CommandDispatcher.java`](src/main/java/com/minecraftbot/baritone/CommandDispatcher.java)

Central command routing component that:
- Dispatches commands through registered handlers
- Falls back to legacy processing for unmigrated commands
- Implements rate limiting and timeout monitoring
- Provides unified error handling

#### Command Handlers

Handlers implement the `CommandHandler` interface and process specific command types:

- **Movement**: `GotoCommandHandler`, navigation and pathfinding
- **Mining**: `MineCommandHandler`, `DigBlockCommandHandler`, and
  `SetFastBreakCommandHandler` for resource extraction and verified client-side
  mining acceleration
- **Building**: `BuildCommandHandler`, schematic construction
- **Inventory**: `InventoryCommandHandler`, item management
- **State**: `StateCommandHandler`, game state queries
- **Mission**: `MissionCommandHandler`, automation workflows

Each handler:
- Validates input parameters
- Interacts with Baritone APIs
- Returns structured JSON responses
- Handles errors gracefully

### Upload Manager [`UploadManager.java`](src/main/java/com/minecraftbot/baritone/UploadManager.java)

Manages schematic file uploads with advanced features:

**Key Features:**
- Chunked upload support for large files
- SHA256 integrity validation
- Priority-based queuing system
- Automatic cleanup of expired uploads
- Progress tracking and statistics

**Upload Process:**
1. `schematic_init`: Initialize upload with metadata
2. `schematic_chunk`: Stream file data in chunks
3. `schematic_commit`: Finalize upload with validation

**Priority Levels:**
- LOW, NORMAL, HIGH, CRITICAL (affects processing order)

### Event Manager [`EventManager.java`](src/main/java/com/minecraftbot/baritone/EventManager.java)

Handles asynchronous event publishing with buffering and subscription capabilities.

**Supported Events:**
- `chat`: Player and system messages
- `block_interact`: Block interaction events
- `entity_spawn/despawn/move`: Entity lifecycle events
- `pathfinding_state`: Baritone navigation status
- `mission`: Automation workflow events
- `tick_update`: Periodic game state updates
- `dimension_change`: World dimension transitions
- `inventory_change`: Item pickup/drops
- `death/respawn`: Player death events
- `damage`: Health change events

**Features:**
- Configurable buffer size (default 100 events)
- TTL-based cleanup (default 5 minutes)
- Priority levels (LOW, NORMAL, HIGH, CRITICAL)
- Push and poll-based event delivery
- Thread-safe operations

### Mission Controller [`MissionController.java`](src/main/java/com/minecraftbot/baritone/MissionController.java)

Manages high-level automation workflows with state persistence.

**Mission Phases:**
- `idle`: Initial state
- `bootstrap`: Bridge initialization
- `base_established`: Starter base setup
- `resource_gathering`: Resource collection
- `nether_ready`: Nether portal preparation
- `eyes_ready`: End portal materials
- `stronghold_hunt`: Stronghold location
- `final_battle`: Dragon fight

**Features:**
- Macro execution system
- Phase progression with prerequisites
- Queue-based command sequencing
- Retry logic and error recovery
- State persistence across sessions

## TCP Communication Protocol

The bridge uses JSON-RPC 2.0 over TCP for communication.

### Message Format

**Request:**
```json
{
  "id": "unique_request_id",
  "command": "command_name",
  "params": {
    "param1": "value1",
    "param2": 123
  }
}
```

**Response:**
```json
{
  "id": "unique_request_id",
  "seq": 12345,
  "timestamp": 1640995200000,
  "status": "ok",
  "data": {
    "result": "command_output"
  }
}
```

### Sequence Numbers

Each response includes a monotonically increasing `seq` number for:
- Request-response correlation
- Detecting missed messages
- Ensuring ordered processing

### Error Handling

Error responses include:
```json
{
  "status": "error",
  "error": "Error message",
  "error_code": "optional_error_code"
}
```

## Integration with Python Client

The Python client connects to the bridge via TCP transport:

1. **Connection**: Establishes TCP connection to port 5555
2. **Authentication**: None. Keep the bridge on loopback/trusted local
   transport only; never publish it as a remote control surface.
3. **Command Dispatch**: Sends JSON-RPC requests for Baritone operations
4. **Event Streaming**: Polls or subscribes to game events
5. **Upload Handling**: Manages schematic uploads for building operations

### Transport Limitations

- TCP transport supports basic command dispatch
- Event support is polling-based (use WebSocket transport for real-time events)
- No built-in reconnection logic (must be handled by client)

## Prerequisites

1. **JDK 25**: Required for the configured Minecraft 26.2 compilation target
2. **Fabric API**: Minecraft modding framework
3. **Baritone API JAR**: The pinned compatibility JAR is tracked at
   `libs/baritone-api-fabric-1.15.0-9-gd93f1582.jar`
   - Upstream Baritone commit: `d93f1582`
   - Target: Minecraft 26.2; license: LGPL-3.0
   - SHA-256: `86d06fce44e8c2da1a46eb44fb7d934fe3712ce63738ecd5be5c68f03f71a9cf`
   - `scripts/check_repository_hygiene.py` verifies its identity before builds
   - See [`libs/README.md`](libs/README.md) for provenance and update procedure

## Building

Open a terminal in the `bridge/` directory:

### Windows
```powershell
.\gradlew.bat --no-daemon --console=plain build
```

### Linux/Mac
```bash
./gradlew --no-daemon --console=plain build
```

The built JAR will be in `build/libs/baritone-api-bridge-1.0.31.jar`

## Installation

1. Copy `baritone-api-bridge-1.0.31.jar` to your Minecraft `mods` folder
2. Start Minecraft with Fabric loader
3. Bridge automatically starts TCP server on port 5555

## Testing

Run the test suite:
```bash
./gradlew test
```

Tests cover:
- Command handler functionality
- Upload manager operations
- Event manager buffering
- Mission controller state management

## Troubleshooting

### Common Issues

1. **Build fails with missing JAR**: Ensure `baritone-api-fabric-1.15.0-9-gd93f1582.jar` is in `libs/` and `gradle/wrapper/gradle-wrapper.jar` exists
2. **Connection refused**: Check Minecraft is running with the mod installed
3. **Commands not responding**: Verify Baritone is properly loaded in Minecraft
4. **Upload timeouts**: Large schematics may need extended timeouts

### Logs

Bridge logs are written to the Minecraft log file. Enable debug logging by setting log level to DEBUG in your logging configuration.

## Development

The bridge follows these patterns:
- Handler pattern for command extensibility
- Thread-safe event buffering
- Builder pattern for complex operations
- TTL-based resource cleanup

**Important**: `bridge/` is the tracked first-party Fabric bridge. Follow its
local `AGENTS.md`; keep source changes bridge-scoped and validate with Java 25.
