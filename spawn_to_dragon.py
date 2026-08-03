"""
spawn_to_dragon.py - Main entrypoint for Minecraft End-to-End Automation.
Usage: python spawn_to_dragon.py [--host HOST] [--port PORT]
"""

import argparse
import logging
import sys
import os
import time

# Progress prints must reach redirected logs immediately; the default block
# buffering hides hours of output when stdout is not a terminal.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except AttributeError:
    pass

# Add src to sys.path to allow imports when run from root
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from baritone_client import Client, TcpTransport
from baritone_client.automator import EndGameAutomator
from baritone_client.world_identity import read_level_dat_seed


def _resolve_world_seed(explicit_seed, host):
    """Resolve a dedicated-server seed without inventing checkpoint identity."""
    if explicit_seed is not None:
        return int(explicit_seed)
    configured = os.environ.get("MC_WORLD_SEED")
    if configured:
        try:
            return int(configured)
        except ValueError:
            raise ValueError("MC_WORLD_SEED must be an integer")
    if str(host).lower() in {"localhost", "127.0.0.1", "::1"}:
        local_level = os.path.join(
            os.path.dirname(__file__), "local_server", "world", "level.dat"
        )
        return read_level_dat_seed(local_level)
    return None


def _configure_logging(verbose: bool = False) -> None:
    """Route baritone_client's own logs to stdout so operators can see them.

    Nothing configured logging, so Python fell back to its last-resort
    handler, which drops anything below WARNING. Every logger.info in the
    common/ gameplay layer was therefore discarded -- including the messages
    that say what a bot is currently doing. A bot could sit motionless for
    hours inside a helper whose progress logs simply never reached the run
    log, leaving only print() output to diagnose from. Live 2026-08-03: three
    bots stalled in find_nether_fortress and not one of its INFO lines,
    including "Starting Nether fortress scan", appeared anywhere.

    Scoped to the package: the root logger stays at WARNING so third-party
    libraries do not flood the run log.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    )
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(handler)
        root.setLevel(logging.WARNING)
    package = logging.getLogger("baritone_client")
    package.setLevel(logging.DEBUG if verbose else logging.INFO)


def main():
    parser = argparse.ArgumentParser(
        description="Minecraft Industrialization Agent - Survival Automation (Hour 0-10)",
        epilog="Mission: Convert a fresh world into a fully industrialized platform. No decorative building allowed."
    )

    parser.add_argument("--host", default="localhost", help="Bridge host")
    parser.add_argument("--port", type=int, default=5555, help="Bridge port")
    parser.add_argument("--timeout", type=float, default=15.0, help="Transport timeout in seconds (for bridge responses)")
    parser.add_argument(
        "--world-seed",
        type=int,
        default=None,
        help="Authoritative dedicated-server seed when the client bridge cannot report it",
    )
    parser.add_argument(
        "--run-dir",
        default=None,
        help="Isolated directory for checkpoints and telemetry (default: current directory)",
    )
    parser.add_argument("--resume", action="store_true", default=True, help="Resume from checkpoint (default; kept for compatibility)")
    parser.add_argument("--fresh", action="store_true", help="Ignore any existing checkpoint and start from scratch")
    parser.add_argument("--suite", type=str, help="Run specific test suite (e.g. T900, T901). Overrides normal automation.")
    parser.add_argument(
        "--no-background-systems",
        action="store_true",
        help=(
            "Disable safety/hunger monitor threads for low-contention runs; "
            "the low-frequency passive landmark mapper remains enabled."
        ),
    )
    parser.add_argument(
        "--no-screenshots",
        action="store_true",
        help="Disable phase-transition screenshots (recommended for headless CI workers).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Include DEBUG-level client logs in the run log.",
    )
    args = parser.parse_args()
    _configure_logging(verbose=args.verbose)

    # Keep legacy WorldState checkpoint files in the same isolated directory
    # as the production StateManager checkpoint.  Without this, parallel bots
    # all read/write checkpoint_storage.json in the repository root.
    if args.run_dir:
        os.environ["MC_RUN_DIR"] = os.path.abspath(args.run_dir)

    print(f"Connecting to Baritone Bridge at {args.host}:{args.port}...")
    
    try:
        print("Waiting for Bridge connection and Player...")
        transport = None
        client = None
        
        while True:
            try:
                if client:
                    try:
                        client.shutdown()
                    except: pass
                
                transport = TcpTransport(host=args.host, port=args.port, timeout=args.timeout)
                client = Client(transport)
                
                state = client.transport.dispatch("get_state", {})
                if 'error' not in state:
                    break
                print(f"Waiting for player... State: {state.get('error')}")
            except Exception as e:
                print(f"Waiting for connection... ({e})")
            
            time.sleep(5.0)
        
        print("Connected successfully!")
        world_seed = _resolve_world_seed(args.world_seed, args.host)
        if world_seed is not None:
            print("Dedicated-server world seed loaded for checkpoint identity.")
        
        # Initialize automator
        automator = EndGameAutomator(
            client,
            checkpoint_dir=args.run_dir,
            screenshot_enabled=not args.no_screenshots,
            world_seed_override=world_seed,
        )
        automator.register_default_handlers()
        if args.no_background_systems:
            automator.systems = [
                system
                for system in automator.systems
                if system.__class__.__name__ == "MappingSystem"
            ]
        
        # Define callbacks for logging
        def on_phase_start(phase):
            print(f"\n>>> Starting Phase: {phase.name}")
            
        def on_phase_complete(phase):
            payload = automator.state.get_phase_payload(phase)
            print(f">>> Completed Phase: {phase.name}")
            if payload:
                print(f"    Details: {payload}")
            
        def on_phase_fail(phase):
            payload = automator.state.get_phase_payload(phase)
            print(f"!!! Failed Phase: {phase.name}")
            if payload:
                print(f"    Failure context: {payload}")

        automator.on_phase_start = on_phase_start
        automator.on_phase_complete = on_phase_complete
        automator.on_phase_fail = on_phase_fail
        
        # Run automation
        if args.suite:
            success = automator.run_suite(args.suite)
        else:
            success = automator.run(resume=not args.fresh)
        
        if success:
            print("\nMISSION ACCOMPLISHED!")
        else:
            print("\nAutomation stopped or failed.")
            
    except ConnectionRefusedError:
        print(f"Error: Connection refused at {args.host}:{args.port}. Is the Minecraft mod running?")
    except KeyboardInterrupt:
        print("\nStopping automation...")
        if 'automator' in locals():
            automator.stop()
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'client' in locals():
            client.shutdown()


if __name__ == "__main__":
    main()
