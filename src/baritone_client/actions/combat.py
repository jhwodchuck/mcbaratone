"""Action-layer adapter for the canonical combat implementation."""

from typing import Dict, List, Optional

from baritone_client.actions.base import BaseAction
from baritone_client.common import combat as common_combat
from baritone_client.core.interfaces import ActionContext, ActionResult


class CombatAction(BaseAction):
    """Expose common combat behavior through the action framework.

    Combat policy, equipment selection, target tracking, retreat thresholds,
    and healing live in ``baritone_client.common.combat``.  Keeping this class
    as a thin adapter prevents action and automator callers from drifting into
    different self-defense implementations.
    """

    def execute(self, context: ActionContext) -> ActionResult:
        return ActionResult.fail("CombatAction requires a specific method call")

    def get_nearby_entities(
        self,
        context: ActionContext,
        radius: int = 30,
    ) -> List[Dict]:
        """Return nearby entities using the canonical bridge-query wrapper."""
        return common_combat.get_nearby_entities(context.client, radius)

    def find_entity_by_type(
        self,
        context: ActionContext,
        entity_types: List[str],
        radius: int = 30,
    ) -> Optional[Dict]:
        """Return the nearest matching entity."""
        return common_combat.find_entity_by_type(
            context.client,
            entity_types,
            radius=radius,
        )

    def attack_nearest(
        self,
        context: ActionContext,
        entity_types: List[str],
        max_range: int = 10,
    ) -> bool:
        """Attack the nearest matching entity through the canonical policy."""
        return common_combat.attack_nearest(
            context.client,
            entity_types,
            max_range=max_range,
        )

    def _look_at_entity(self, context: ActionContext, entity: Dict) -> bool:
        """Preserve the former action helper as a compatibility adapter."""
        return common_combat.look_at_entity(context.client, entity)

    def safe_combat(
        self,
        context: ActionContext,
        target_id: int,
        retreat_health: float = 6.0,
        max_duration: int = 30,
    ) -> bool:
        """Run bounded combat through the canonical implementation."""
        return common_combat.safe_combat(
            context.client,
            target_id,
            retreat_health=retreat_health,
            max_duration=max_duration,
        )

    def hunt_passive_mobs(
        self,
        context: ActionContext,
        target_mobs: Optional[List[str]] = None,
        target_count: int = 10,
        target_loot: Optional[Dict[str, int]] = None,
        timeout: int = 300,
    ) -> ActionResult:
        """Hunt passive mobs and adapt ``TaskResult`` to ``ActionResult``."""
        mob_types = target_mobs or ["pig", "cow", "sheep", "chicken"]
        result = common_combat.hunt_mobs(
            context.client,
            mob_types=mob_types,
            required_loot=target_loot or {},
            search_radius=50,
            timeout=timeout,
            heal_threshold=5.0,
            target_kills=target_count if not target_loot else None,
        )
        reason = getattr(result, "reason", getattr(result, "message", "Hunt complete"))
        data = getattr(result, "data", {})
        if result.success:
            return ActionResult.ok(reason, **data)
        return ActionResult.fail(reason, **data)

    def heal_if_needed(
        self,
        context: ActionContext,
        threshold: float = 10.0,
    ) -> bool:
        """Use the canonical carried-food recovery behavior."""
        return common_combat.heal_if_needed(context.client, threshold=threshold)


__all__ = ["CombatAction"]
