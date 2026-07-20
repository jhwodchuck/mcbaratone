"""Compatibility import for the relocated Survival progression suite."""

try:
    from survival.suite import create_extended_suite_1200
except ImportError:
    from tests.functional.survival.suite import create_extended_suite_1200

__all__ = ["create_extended_suite_1200"]
