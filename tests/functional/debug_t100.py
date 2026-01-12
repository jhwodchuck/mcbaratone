
import sys
import os
import traceback

# Add paths
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from test_base import FunctionalHarness, TestContext
from extended_suite_100 import create_extended_suite_100

def main():
    harness = FunctionalHarness(host="localhost", port=5555)
    if not harness.connect():
        print("Failed to connect")
        return

    suite = create_extended_suite_100()
    t100 = next(t for t in suite.tests if t.id == "T100")
    
    print("Running T100 (Setup)...")
    try:
        t100.setup(harness.ctx)
        print("Setup OK")
        print("Running T100 (Step 0)...")
        t100.steps[0](harness.ctx)
        print("Step 0 OK")
    except Exception:
        traceback.print_exc()
    finally:
        harness.disconnect()

if __name__ == "__main__":
    main()
