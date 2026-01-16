"""
Functional Test Runner - Main entry point for running test suites.
Usage: python run_tests.py [--suite SUITE_NAME] [--test TEST_ID]
"""

import argparse
import sys
import os
import time
import re

# Add paths
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..')) # Root for 'tests' package imports

from test_base import FunctionalHarness, FunctionalResult

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
from extended_suite_1000 import create_extended_suite_1000
from extended_suite_1100 import create_extended_suite_1100


def _sanitize_label(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return cleaned.strip("_") or "run"


def _default_log_label(args) -> str:
    if args.test:
        target_ids = [t.strip() for t in args.test.split(",") if t.strip()]
        return "tests_" + "_".join(_sanitize_label(t) for t in target_ids)
    if args.suite:
        return _sanitize_label(args.suite)
    return "all_suites"


def _select_log_file(args) -> str:
    if getattr(args, "log_file", None):
        return args.log_file

    label = _default_log_label(args)
    log_dir = os.path.join(os.path.dirname(__file__), "logs")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(log_dir, f"{label}_{stamp}.log")


class _Tee:
    def __init__(self, stream, log_path: str):
        self._stream = stream
        self._handle = open(log_path, "a", encoding="utf-8")

    def write(self, data):
        self._stream.write(data)
        self._handle.write(data)

    def flush(self):
        self._stream.flush()
        self._handle.flush()

    def close(self):
        self._handle.close()


def main():
    parser = argparse.ArgumentParser(description="Minecraft Functional Test Runner")
    parser.add_argument("--suite", help="Run specific suite (e.g., Suite_0_Harness)")
    parser.add_argument("--test", help="Run specific test (e.g., T000)")
    parser.add_argument("--host", default="localhost", help="Bridge host")
    parser.add_argument("--port", type=int, default=5555, help="Bridge port")
    parser.add_argument("--list", action="store_true", help="List all tests")
    parser.add_argument("--repeat", type=int, default=1, help="Run tests N times")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first failure")
    parser.add_argument("--json", action="store_true", help="Output JSON report")
    parser.add_argument("--log-file", help="Write test events to a log file")
    args = parser.parse_args()

    log_file = _select_log_file(args)
    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
    orig_out = sys.stdout
    orig_err = sys.stderr
    tee_out = _Tee(orig_out, log_file)
    tee_err = _Tee(orig_err, log_file)
    sys.stdout = tee_out
    sys.stderr = tee_err

    try:
        # Create harness
        harness = FunctionalHarness(host=args.host, port=args.port)
        
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
        harness.register_suite(create_extended_suite_1000())
        harness.register_suite(create_extended_suite_1100())
        
        # List mode
        if args.list:
            print("\nRegistered Test Suites:")
            print("=" * 60)
            for name, suite in sorted(harness.suites.items()):
                print(f"\n{name}: {suite.description}")
                for test in suite.tests:
                    print(f"  {test.id}: {test.name}")
                    print(f"       {test.description}")
            return
        
        # Connect
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Runner start")
        print(f"Connecting to {args.host}:{args.port}...")
        if not harness.connect():
            print("Failed to connect. Is Minecraft running with the bridge mod?")
            sys.exit(1)

        print("Connected!\n")
        try:
            version = harness.client.transport.dispatch("get_version", {})
            data = version.get("data", version)
            print(f"Bridge version: {data.get('mod_version', 'unknown')}")
        except Exception as e:
            print(f"Bridge version: unknown ({e})")
        
        if log_file and harness.ctx:
            harness.ctx.set_log_file(log_file)
            print(f"Logging test events to: {log_file}")

        overall_success = True

        try:
            # Repeat loop
            for run_idx in range(args.repeat):
                if args.repeat > 1:
                    print(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] Run {run_idx + 1}/{args.repeat} start")

                current_run_results = {} # {suite_name: {test_id: Result}}

                if args.test:
                    # Run specific tests (comma-separated)
                    target_ids = [t.strip() for t in args.test.split(",")]
                    found_count = 0
                    
                    for suite in harness.suites.values():
                        for test in suite.tests:
                            if test.id in target_ids:
                                print(f"Running test: {test.id}")
                                res, msg, events = test.run(harness.ctx)
                                print(f"Result: {res.value} - {msg}")
                                
                                if res != FunctionalResult.PASS and events:
                                    print("  Events:")
                                    for e in events:
                                        print(f"    {e}")
                                
                                current_run_results.setdefault(suite.name, {})[test.id] = (res, msg)
                                found_count += 1

                    if found_count < len(target_ids):
                        print(f"Warning: Only found {found_count} out of {len(target_ids)} requested tests.")
                        # identifying missing ones is harder without extra logic, but this is sufficient for now.
                        
                    if found_count == 0:
                         print(f"No tests found matching: {args.test}")
                         overall_success = False
                         continue

                elif args.suite:
                    # Run specific suite
                    if args.suite in harness.suites:
                        suite_results = harness.run_suite(args.suite)
                        current_run_results[args.suite] = suite_results
                    else:
                        print(f"Suite {args.suite} not found")
                        print(f"Available: {list(harness.suites.keys())}")
                        overall_success = False
                        continue
                        
                else:
                    # Run all suites
                    current_run_results = harness.run_all()

                # Process Results for this Run
                # 1. Print Summary
                harness.print_summary(current_run_results)

                # 2. JSON Report
                if args.json:
                    harness.save_json_report(current_run_results)

                # 3. Check Failures
                run_has_failures = False
                for suite_res in current_run_results.values():
                    for result, _msg in suite_res.values():
                        result_val = result.value if hasattr(result, "value") else result
                        if str(result_val) != FunctionalResult.PASS.value:
                            run_has_failures = True
                            break
                    if run_has_failures:
                        break
                
                if run_has_failures:
                    overall_success = False
                    if args.fail_fast:
                        print("\nFail-fast triggered.")
                        break
                if args.repeat > 1:
                    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Run {run_idx + 1}/{args.repeat} end")

        except KeyboardInterrupt:
            print("\nInterrupted by user.")
            overall_success = False
        finally:
            harness.disconnect()
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Runner end")
    finally:
        sys.stdout = orig_out
        sys.stderr = orig_err
        try:
            tee_out.flush()
            tee_err.flush()
        finally:
            tee_out.close()
            tee_err.close()

    sys.exit(0 if overall_success else 1)


if __name__ == "__main__":
    main()
