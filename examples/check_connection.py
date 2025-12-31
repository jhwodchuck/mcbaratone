import logging
import sys
import time
from baritone_client import Client, Py4JTransport, TcpTransport
from baritone_client.exceptions import TransportError

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("ConnectionCheck")

def check_py4j(port=25333):
    logger.info(f"Attempting to connect via Py4J at port {port}...")
    try:
        transport = Py4JTransport(gateway_params={"gateway_parameters": {"port": port}})
        client = Client(transport)
        # Try to access entry point
        logger.info("Gateway connected. Checking entry point...")
        try:
             # Just checking if we can serialize a command
             client.command.run("echo Hello from Python")
             logger.info("Successfully dispatched command. Bridge is active.")
             return True
        except Exception as e:
             logger.warning(f"Connected, but failed to dispatch command: {e}")
             return True
    except Exception as e:
        logger.error(f"Py4J connection failed: {e}")
        return False

def check_tcp(host="localhost", port=5555):
    logger.info(f"Attempting to connect via TCP Bridge at {host}:{port}...")
    try:
        transport = TcpTransport(host=host, port=port)
        client = Client(transport)
        # Try a simple command
        logger.info("TCP socket connected. Sending handshake/status check...")
        try:
             # Bridge command: get_state
             state = client.transport.dispatch("process/status", {})
             pos = state.get("position", {})
             logger.info(f"Successfully retrieved state. Position: {pos}")
             return True
        except Exception as e:
             logger.warning(f"Connected, but failed to retrieve state: {e}")
             return True
    except Exception as e:
        logger.error(f"TCP connection failed: {e}")
        return False

if __name__ == "__main__":
    logger.info("--- Baritone Python Client Connection Check ---")
    
    tcp_success = check_tcp()
    if tcp_success:
        sys.exit(0)

    logger.info("-" * 40)
    
    py4j_success = check_py4j()
    if py4j_success:
        sys.exit(0)

    logger.info("-" * 40)
    logger.error("Could not connect to Baritone via TCP Bridge or Py4J.")
    logger.info("Troubleshooting:")
    logger.info("1. Ensure Minecraft is running.")
    logger.info("2. Ensure the 'Baritone Bridge' mod (Java) is installed and active.")
    logger.info("3. Check if the bridge is listening on TCP port 5555 or Py4J port 25333.")
    sys.exit(1)
