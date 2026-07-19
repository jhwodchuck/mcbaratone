"""Compatibility exports for the relocated Survival evidence helpers."""

try:
    from survival.evidence import *  # noqa: F401,F403
except ImportError:
    from tests.functional.survival.evidence import *  # noqa: F401,F403
