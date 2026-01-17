
import sys
import time
from baritone_client.transport.transport import TcpTransport

def main():
    print("Connecting to bridge via TcpTransport...")
    transport = TcpTransport(host="localhost", port=5555, timeout=10.0)
    print("Connected!")
    
    # Needs a dummy event manager or None, which is default
    
    print("Requesting state...")
    try:
        data = transport.dispatch("get_state", {})
        pos = data.get("player", {}).get("pos")
        print(f"Success! Player pos: {pos}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        transport.shutdown()

if __name__ == "__main__":
    main()
