"""Safety outcomes shared by long-running mining loops."""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class MiningDefenseOutcome:
    """Describe whether defense interrupted mining and why."""

    intervened: bool
    survival_abort: bool


def run_mining_defense(
    client: Any,
    defend: Callable[[Any], bool],
) -> MiningDefenseOutcome:
    """Run one defense tick and identify a submersion-driven intervention.

    The drowning reflex tracks consecutive submerged polls on the client.
    A mining loop sees a positive count before the surfacing tick and either
    zero (air reached) or a larger count (surface failed) afterward. Both mean
    the current mining target must be abandoned instead of resumed.
    """
    submerged_before = int(getattr(client, "_submersion_ticks", 0))
    intervened = bool(defend(client))
    submerged_after = int(getattr(client, "_submersion_ticks", 0))
    survival_abort = intervened and (
        submerged_before > 0 or submerged_after > 0
    )
    return MiningDefenseOutcome(intervened, survival_abort)
