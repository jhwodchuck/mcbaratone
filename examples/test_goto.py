import logging
import sys
import time
from baritone_client import Client, TcpTransport

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("TestGoto")

def test_goto(x, y, z):
    logger.info(f"Connecting to Baritone Bridge...")
    try:
        transport = TcpTransport(host="localhost", port=5555)
        client = Client(transport)
        
        logger.info(f"Sending command: goto {x} {y} {z}")
        # Using the command facade which gets translated by TcpTransport
        response = client.command.run(f"goto {x} {y} {z}")
        
        logger.info(f"Response: {response}")
        
        # TcpTransport unwraps the response and returns the 'data' object.
        # If there was an error, it would have raised an exception.
        if response.get("started") is True:
            logger.info("Command sent successfully!")
            
            # Optional: Monitor state for a few seconds to see if pathing starts
            for _ in range(5):
                time.sleep(1)
                state = client.transport.dispatch("process/status", {})
                is_pathing = state.get("is_pathing", False)
                pos = state.get("position", {})
                logger.info(f"Pathing: {is_pathing}, Pos: {pos}")
                if not is_pathing and _ > 2:
                    break
            return True
        else:
            logger.error(f"Command failed: {response}")
            return False
            
    except Exception as e:
        logger.error(f"Test failed: {e}")
        return False

if __name__ == "__main__":
    # User requested: goto x 0 y 63 z 0
    if test_goto(0, 63, 0):
        sys.exit(0)
    else:
        sys.exit(1)
