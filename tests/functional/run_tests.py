"""
Functional Test Runner - Main entry point for running test suites.
Usage: python run_tests.py [--suite SUITE_NAME] [--test TEST_ID]
"""

import argparse
import sys
import os

# Add paths
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from test_base import FunctionalHarness

# Original suites
from suite_0_1 import create_suite_0, create_suite_1
from suite_2 import create_suite_2
from suite_3_4 import create_suite_3, create_suite_4
from suite_5 import create_suite_5
from suite_6_7_8 import create_suite_6, create_suite_7, create_suite_8

# Extended granular suites
from extended_suite_100 import create_extended_suite_100
from extended_suite_200 import create_extended_suite_200
from extended_suite_300 import create_extended_suite_300
from extended_suite_400 import create_extended_suite_400
from extended_suite_500 import create_extended_suite_500
from extended_suite_600 import create_extended_suite_600
from extended_suite_700 import create_extended_suite_700
from extended_suite_800 import create_extended_suite_800
from extended_suite_900 import create_extended_suite_900


def main():
    parser = argparse.ArgumentParser(description="Minecraft Functional Test Runner")
    parser.add_argument("--suite", help="Run specific suite (e.g., Suite_0_Harness)")
    parser.add_argument("--test", help="Run specific test (e.g., T000)")
    parser.add_argument("--host", default="localhost", help="Bridge host")
    parser.add_argument("--port", type=int, default=5555, help="Bridge port")
    parser.add_argument("--list", action="store_true", help="List all tests")
    args = parser.parse_args()
    
    # Create harness
    harness = FunctionalHarness(host=args.host, port=args.port)
    
    # Register original suites
    harness.register_suite(create_suite_0())
    harness.register_suite(create_suite_1())
    harness.register_suite(create_suite_2())
    harness.register_suite(create_suite_3())
    harness.register_suite(create_suite_4())
    harness.register_suite(create_suite_5())
    harness.register_suite(create_suite_6())
    harness.register_suite(create_suite_7())
    harness.register_suite(create_suite_8())
    
    # Register extended granular suites
    harness.register_suite(create_extended_suite_100())
    harness.register_suite(create_extended_suite_200())
    harness.register_suite(create_extended_suite_300())
    harness.register_suite(create_extended_suite_400())
    harness.register_suite(create_extended_suite_500())
    harness.register_suite(create_extended_suite_600())
    harness.register_suite(create_extended_suite_700())
    harness.register_suite(create_extended_suite_800())
    harness.register_suite(create_extended_suite_900())
    
    # List mode
    if args.list:
        print("\nRegistered Test Suites:")
        print("=" * 60)
        for name, suite in harness.suites.items():
            print(f"\n{name}: {suite.description}")
            for test in suite.tests:
                print(f"  {test.id}: {test.name}")
                print(f"       {test.description}")
        return
    
    # Connect
    print(f"Connecting to {args.host}:{args.port}...")
    if not harness.connect():
        print("Failed to connect. Is Minecraft running with the bridge mod?")
        return
    
    print("Connected!\n")
    try:
        version = harness.client.transport.dispatch("get_version", {})
        data = version.get("data", version)
        print(f"Bridge version: {data.get('mod_version', 'unknown')}")
    except Exception as e:
        print(f"Bridge version: unknown ({e})")
    
    try:
        if args.test:
            # Run specific test
            found = False
            for suite in harness.suites.values():
                for test in suite.tests:
                    if test.id == args.test:
                        print(f"Running single test: {test.id}")
                        result, msg, events = test.run(harness.ctx)
                        print(f"Result: {result.value} - {msg}")
                        if result != result.PASS and events:
                            print("  Events:")
                            for e in events[-5:]:
                                print(f"    {e}")
                        found = True
                        break
            if not found:
                print(f"Test {args.test} not found")
                
        elif args.suite:
            # Run specific suite
            if args.suite in harness.suites:
                results = harness.run_suite(args.suite)
                harness.print_summary({args.suite: results})
            else:
                print(f"Suite {args.suite} not found")
                print(f"Available: {list(harness.suites.keys())}")
                
        else:
            # Run all suites
            results = harness.run_all()
            harness.print_summary(results)
            
    finally:
        harness.disconnect()


if __name__ == "__main__":
    main()
