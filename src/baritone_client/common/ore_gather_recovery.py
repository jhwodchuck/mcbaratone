"""Bounded recovery ladder for stalled ore gathering."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OreStallRecovery:
    """Track exact-target, descent, terrain, and known-mine fallbacks."""

    direct_failures: int = 0
    descent_attempted: bool = False
    dry_relocation_attempted: bool = False
    mine_relocation_attempted: bool = False
    failed_targets: set[tuple[int, int, int]] = field(default_factory=set)

    def _reset_targets(self) -> None:
        self.direct_failures = 0
        self.failed_targets.clear()

    def recover(
        self,
        client: Any,
        ore_type: str,
        ore_blocks: list[str],
        count: int,
    ) -> bool:
        """Run one bounded stall recovery; False means all routes failed."""
        from . import resources as api

        print(f"DEBUG: {ore_type} gathering stalled; trying exact nearby ore...")
        api._serialized_dispatch(
            client,
            "cancel",
            {},
            post_delay_seconds=api._BARITONE_CANCEL_GRACE_SECONDS,
        )
        radius = api._ORE_FALLBACK_RADII[
            min(self.direct_failures, len(api._ORE_FALLBACK_RADII) - 1)
        ]
        target = api._find_safe_nearby_ore(
            client,
            ore_blocks,
            radius=radius,
            excluded_positions=self.failed_targets,
        )
        if target and api._approach_and_break_stone(client, target):
            print(f"DEBUG: Broke nearby {ore_type} ore at {target}")
            self._reset_targets()
            return True

        self.direct_failures += 1
        if target:
            self.failed_targets.add(target)
        if self.direct_failures < api._ORE_FALLBACK_LIMIT:
            api._serialized_dispatch(
                client,
                "mine",
                {"blocks": ore_blocks, "quantity": count + 2},
                post_delay_seconds=api._BARITONE_MINE_SETUP_SECONDS,
            )
            return True

        if not self.descent_attempted:
            self.descent_attempted = True
            if api._descend_to_stone_layer(client):
                self._reset_targets()
                api._start_mine_process(client, ore_blocks, count + 2)
                return True
        if not self.dry_relocation_attempted:
            self.dry_relocation_attempted = True
            if api._relocate_to_dry_stone_terrain(client):
                self.descent_attempted = False
                self._reset_targets()
                api._start_mine_process(client, ore_blocks, count + 2)
                return True
        if not self.mine_relocation_attempted:
            self.mine_relocation_attempted = True
            if api._relocate_to_checkpointed_mine(client):
                self.descent_attempted = False
                self._reset_targets()
                api._start_mine_process(client, ore_blocks, count + 2)
                return True
        print(
            f"DEBUG: No reachable nearby {ore_type} ore after "
            f"{api._ORE_FALLBACK_LIMIT} exact attempts"
        )
        return False
