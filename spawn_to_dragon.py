"""
spawn_to_dragon.py - Main entrypoint for Minecraft End-to-End Automation.
Usage: python spawn_to_dragon.py [--host HOST] [--port PORT]
"""

import argparse
import sys
import os
import time

# Add src to sys.path to allow imports when run from root
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from baritone_client import Client, TcpTransport
from baritone_client.automator import EndGameAutomator


def main():
    parser = argparse.ArgumentParser(
        description="Minecraft Industrialization Agent - Survival Automation (Hour 0-10)",
        epilog="Mission: Convert a fresh world into a fully industrialized platform. No decorative building allowed."
    )
    parser.add_argument("--host", default="localhost", help="Bridge host")
    parser.add_argument("--port", type=int, default=5555, help="Bridge port")
    parser.add_argument("--timeout", type=float, default=15.0, help="Transport timeout in seconds (for bridge responses)")
    parser.add_argument("--resume", action="store_true", default=True, help="Resume from checkpoint")
    args = parser.parse_args()

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
        
        # Initialize automator
        automator = EndGameAutomator(client)
        automator.register_default_handlers()
        
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
        success = automator.run(resume=args.resume)
        
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
