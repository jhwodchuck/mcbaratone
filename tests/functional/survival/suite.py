"""Suite 1200: read-only Survival progression acceptance gates.

Unlike the arena-style suites, these tests do not give items, teleport, change
time, set blocks, or otherwise drive Minecraft.  They are intended to observe
the world produced by the autonomous runner, one milestone at a time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    from test_base import TestCase, TestSuite, TestContext
except ImportError:  # Package import under pytest.
    from tests.functional.test_base import TestCase, TestSuite, TestContext

from .evidence import PROGRESSION_GATES, ProgressionEvidence


def create_extended_suite_1200(checkpoint_path: Optional[str | Path] = None) -> TestSuite:
    """Build the spawn-to-endgame observer suite.

    The optional checkpoint path makes the suite deterministic when more than
    one world or runner exists.  If omitted, the helper checks the
    ``MCBARATONE_CHECKPOINT`` environment variable and then the repository root.
    """
    suite = TestSuite(
        "Suite_1200_Survival_Progression",
        "Read-only acceptance gates for a no-cheat spawn-to-endgame run",
    )

    for gate in PROGRESSION_GATES:
        def assertion(ctx: TestContext, gate=gate):
            evidence = ProgressionEvidence.capture(ctx, checkpoint_path=checkpoint_path)
            return gate.evaluate(evidence)

        assertion.__name__ = f"assert_{gate.id.lower()}"
        suite.add(
            TestCase(
                id=gate.id,
                name=gate.name,
                description=f"{gate.description} [read-only; production phase: {gate.production_phase or 'n/a'}]",
                timeout_seconds=30,
                steps=[],
                assertions=[assertion],
            )
        )

    return suite


__all__ = ["create_extended_suite_1200"]
