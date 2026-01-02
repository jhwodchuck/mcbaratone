#!/usr/bin/env python3
"""
Spawn to Dragon - End-to-end Minecraft automation.

This script orchestrates a complete playthrough from spawn to defeating the Ender Dragon.
It uses the Baritone bridge for movement/mining and the unified EndGameAutomator framework.

Usage:
    python spawn_to_dragon.py [--resume] [--host HOST] [--port PORT]

Options:
    --resume    Resume from last checkpoint
    --host      Bridge host (default: localhost)
    --port      Bridge port (default: 5555)
"""

import argparse
import sys
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baritone_client import Client
from baritone_client.transport import TcpTransport
from baritone_client.automator import EndGameAutomator


def run_automation(client, resume: bool = False):
    """Run the full automation pipeline using the unified EndGameAutomator."""
    # Create and configure automator
    automator = EndGameAutomator(client)
    automator.register_default_handlers()

    # Define callbacks for logging
    def on_phase_start(phase):
        print(f"\n>>> Starting Phase: {phase.name}")

    def on_phase_complete(phase):
        print(f">>> Completed Phase: {phase.name}")

    def on_phase_fail(phase):
        print(f"!!! Failed Phase: {phase.name}")

    automator.on_phase_start = on_phase_start
    automator.on_phase_complete = on_phase_complete
    automator.on_phase_fail = on_phase_fail

    # Run automation
    return automator.run(resume=resume)


def main():
    parser = argparse.ArgumentParser(description="Spawn to Dragon Automation")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument("--host", default="localhost", help="Bridge host")
    parser.add_argument("--port", type=int, default=5555, help="Bridge port")
    parser.add_argument("--timeout", type=float, default=15.0, help="Transport timeout in seconds")

    args = parser.parse_args()

    print(f"Connecting to bridge at {args.host}:{args.port}...")

    try:
        transport = TcpTransport(host=args.host, port=args.port, timeout=args.timeout)
        client = Client(transport)

        print("Connected!")

        success = run_automation(client, resume=args.resume)

        client.shutdown()
        sys.exit(0 if success else 1)

    except ConnectionRefusedError:
        print("Could not connect to bridge. Is Minecraft running with the mod?")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()