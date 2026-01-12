
import sys
import os

# Add paths
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from test_base import FunctionalHarness
from validate_harness_refactor import create_harness_validation_suite

def main():
    # Use default host/port
    harness = FunctionalHarness(host="localhost", port=5555)
    
    print("Connecting to bridge...")
    if not harness.connect():
        print("Failed to connect to Minecraft bridge.")
        return

    print("Connected. Running validation suite...")
    
    harness.register_suite(create_harness_validation_suite())
    results = harness.run_suite("Harness_Validation")
    harness.print_summary({"Harness_Validation": results})
    harness.disconnect()

if __name__ == "__main__":
    main()
