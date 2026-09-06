"""Supervised melee execution behind :func:`combat.safe_combat`."""

import time
from functools import partial

from . import combat as api
from .combat_action import dispatch_held_item_use, exclusive_combat_action

# Cover one blaze fireball volley without delaying every sword swing by nearly
# two seconds. Live Bot16 reduced a blaze to 2 HP, then died because the old
# 1.8-second shield pulse postponed the finishing hit past the retreat floor.
SHIELD_HOLD_MS = 900
SHIELD_REFRESH_SECONDS = 1.1


def _inventory_payload(response):
    """Unwrap bridge inventory responses without depending on one transport."""
    if not isinstance(response, dict):
        return {}
    data = response.get("data")
    return data if isinstance(data, dict) else response


def _has_shield(entries) -> bool:
    return any(
        isinstance(item, dict)
        and item.get("id") == "minecraft:shield"
        and int(item.get("count", 0) or 0) > 0
        for item in (entries or [])
    )


def _prepare_shield(client) -> bool:
    """Equip and verify a carried shield for the current fight."""
    try:
        with exclusive_combat_action(client) as acquired:
            if not acquired:
                return False
            inventory = _inventory_payload(
                client.transport.dispatch("get_inventory", {})
            )
            if _has_shield(inventory.get("offhand")):
                return True
            if not _has_shield(inventory.get("inventory")):
                return False
            client.transport.dispatch(
                "equip",
                {"slot": "offhand", "item": "minecraft:shield"},
            )
            refreshed = _inventory_payload(
                client.transport.dispatch("get_inventory", {})
            )
            return _has_shield(refreshed.get("offhand"))
    except Exception as exc:
        print(f"COMBAT: shield preparation failed: {exc}")
        return False


def _is_ranged_target(target, state) -> bool:
    assessments = api.assess_threats([target], state)
    return bool(assessments and assessments[0].style == api.AttackStyle.RANGED)


def _prepare_combat_shield(client, target, state, shield) -> bool:
    """Prepare a carried shield for the current fight, whatever it is.

    Previously gated on _is_ranged_target, so shield use existed only
    against skeletons and blazes. A vanilla shield blocks melee damage
    just as well as projectiles, but zombies -- ordinary melee mobs --
    never got it: live A1 2026-09-06 death telemetry showed
    is_blocking=false in every one of 15 deaths, including 6 to zombies,
    despite a shield in the off-hand in most of them. There is no
    attack-style reason to withhold this from a melee fight.
    """
    if not shield["checked"]:
        shield["ready"] = _prepare_shield(client)
        shield["checked"] = True
    return bool(shield["ready"])


def _verified_shield_hold(result) -> bool:
    """Require bridge proof that the offhand shield began its held use."""
    data = _inventory_payload(result)
    return bool(
        isinstance(data, dict)
        and data.get("holding") is True
        and data.get("hand") == "OFF_HAND"
        and data.get("active_hand") == "OFF_HAND"
        and data.get("held_item") == "minecraft:shield"
        and data.get("is_using_item") is True
    )


def _boss_attack_authorized(client, target, state) -> bool:
    """Require explicit, exact intent before provoking a boss."""
    assessments = api.assess_threats([target], state)
    from .combat_intent import (
        boss_action_context_allowed,
        current_combat_intent,
    )
    from .combat_targeting import normalize_mob_type

    target_type = normalize_mob_type(target.get("type"))
    boss_action = bool(
        (assessments and assessments[0].style == api.AttackStyle.BOSS)
        or target_type == "end_crystal"
    )
    if not boss_action:
        return True
    if not boss_action_context_allowed(target, state):
        return False

    intent = current_combat_intent(client)
    return bool(
        intent is not None
        and intent.allow_boss
        and intent.authorizes(target)
    )


def _equip_target_weapon(client, target) -> bool:
    """Equip a fresh target-aware weapon through the shared loadout policy."""
    try:
        return bool(api.equip_best_weapon(client, target.get("type", "")))
    except TypeError:  # compatibility with one-argument injected fakes
        return bool(api.equip_best_weapon(client))


def _combat_intervention(client, snapshot, state, *, no_retreat):
    """Return a fail-closed reason before an approach or attack."""
    skipped = api._snapshot_skipped_count(snapshot)
    if skipped:
        reason = "incomplete_snapshot"
        fields = {"skipped_count": skipped}
    else:
        food = state.get("food_level", state.get("food"))
        try:
            hungry = food is not None and int(food) < 7
        except (TypeError, ValueError):
            hungry = False
        if no_retreat or not hungry:
            return None
        reason = "retreat_hunger"
        fields = {"food": food}
    api.combat_telemetry.record_combat_action(
        client,
        "combat_intervention",
        outcome=reason,
        **fields,
    )
    api._stop_for_defense(client)
    return reason


def execute_melee_strike(client, target, state, *, min_cooldown=None):
    """Attempt one cooldown-verified strike through the canonical boundary."""
    target_id = target.get("id")
    if target_id is None:
        return {"attacked": False, "reason": "target_id_missing"}
    if not _boss_attack_authorized(client, target, state):
        return {"attacked": False, "reason": "boss_not_authorized"}
    threshold = (
        api.MELEE_ATTACK_COOLDOWN_THRESHOLD
        if min_cooldown is None
        else float(min_cooldown)
    )
    if api._attack_cooldown(state) < threshold:
        return {"attacked": False, "reason": "cooldown", "local_check": True}
    with exclusive_combat_action(client) as acquired:
        if not acquired:
            return {"attacked": False, "reason": "action_busy"}
        if not _equip_target_weapon(client, target):
            return {"attacked": False, "reason": "weapon_unavailable"}
        api.look_at_entity(client, target)
        result = client.transport.dispatch(
            "attack_entity",
            {"entity_id": int(target_id), "min_cooldown": threshold},
        )
    api.combat_telemetry.record_melee_attack(client, target, result)
    return result


def _supervise_approach(
    client,
    *,
    target_id,
    retreat_health,
    tracking_radius,
    no_retreat,
    abort_on_other_hostiles,
    intervention,
    shielding,
    shield,
) -> bool:
    """Keep target approaches survival-aware and sensitive to new threats."""
    state = client.transport.dispatch("get_state", {})
    api.ensure_alive(client, state)
    health = float(state.get("health", 20.0) or 0)
    if not no_retreat and health < retreat_health:
        intervention["reason"] = "retreat_health"
        return True
    food = state.get("food_level", state.get("food"))
    try:
        hungry = food is not None and int(food) < 7
    except (TypeError, ValueError):
        hungry = False
    if not no_retreat and hungry:
        intervention["reason"] = "retreat_hunger"
        api._stop_for_defense(client)
        return True
    if abort_on_other_hostiles:
        try:
            entities = api.get_nearby_entities(
                client,
                radius=max(30, tracking_radius),
                raise_on_error=True,
            )
        except api.EntityQueryError:
            intervention["reason"] = "entity_query_unavailable"
            return True
        other = next(
            (
                threat
                for threat in api.assess_threats(entities, state)
                if threat.entity.get("id") != target_id
                and threat.distance <= api.MULTI_THREAT_ABORT_RADIUS
            ),
            None,
        )
        if other is not None:
            intervention["reason"] = "secondary_hostile"
            api.combat_telemetry.record_combat_action(
                client,
                "approach_interrupted",
                outcome="secondary_hostile",
                target=other.entity,
            )
            return True
    now = time.monotonic()
    if (
        shielding["active"]
        and shield["ready"]
        and now >= shield["refresh_at"]
    ):
        with exclusive_combat_action(client) as acquired:
            if not acquired:
                return False
            result = dispatch_held_item_use(
                client, SHIELD_HOLD_MS, hand="OFF_HAND"
            )
        if not _verified_shield_hold(result):
            shield["ready"] = False
            intervention["reason"] = "shield_use_unverified"
            api.combat_telemetry.record_combat_action(
                client,
                "shield_hold",
                outcome="unverified",
                bridge_result=result,
            )
            return True
        api.combat_telemetry.record_combat_action(
            client,
            "shield_hold",
            outcome="started",
            bridge_result=result,
        )
        released_at = time.monotonic()
        shield["blocked_until"] = released_at
        shield["refresh_at"] = released_at + SHIELD_REFRESH_SECONDS
    return False


def execute_safe_combat(
    client,
    target_id: int,
    retreat_health: float,
    max_duration: int,
    abort_on_other_hostiles: bool,
    tracking_radius: int,
    no_retreat: bool,
) -> bool:
    """Fight one target while preserving survival and truthful outcomes."""
    intervention = {"reason": None}
    shielding = {"active": False}
    shield = {
        "checked": False,
        "ready": False,
        "blocked_until": 0.0,
        "refresh_at": 0.0,
    }

    def finish(reason: str, result: bool = False) -> bool:
        api.combat_telemetry.get_combat_telemetry(
            client
        ).set_disengagement_reason(reason)
        return result

    navigation_watchdog = partial(
        _supervise_approach,
        client,
        target_id=target_id,
        retreat_health=retreat_health,
        tracking_radius=tracking_radius,
        no_retreat=no_retreat,
        abort_on_other_hostiles=abort_on_other_hostiles,
        intervention=intervention,
        shielding=shielding,
        shield=shield,
    )

    started_at = time.time()
    approach_failures = 0
    while time.time() - started_at < max_duration:
        snapshot = api._get_combat_snapshot(
            client,
            radius=max(30, tracking_radius),
        )
        if snapshot is not None:
            state = snapshot.get("player", {})
            entities = snapshot.get("entities", [])
        else:
            state = client.transport.dispatch("get_state", {})
            entities = api.get_nearby_entities(
                client,
                radius=max(30, tracking_radius),
            )
        api.combat_telemetry.record_combat_snapshot(
            client, {"player": state, "entities": entities}
        )
        api.ensure_alive(client, state)
        intervention_reason = _combat_intervention(
            client, snapshot, state, no_retreat=no_retreat
        )
        if intervention_reason is not None:
            return finish(intervention_reason)

        if api._submerged_too_long(client, state, max_seconds=8.0):
            print(
                "SURVIVAL: submerged too long mid-combat; "
                "surfacing to avoid drowning"
            )
            client.transport.dispatch("cancel", {})
            api._surface_after_aquatic_hunt(client, timeout=8.0)
            client._submerged_since = None
            return finish("survival_intervention")

        health = state.get("health", 20)
        if health < retreat_health and not no_retreat:
            print(f"Retreating! Health: {health}")
            client.transport.dispatch("cancel", {})
            return finish("retreat_health")

        target = next(
            (entity for entity in entities if entity.get("id") == target_id),
            None,
        )
        if abort_on_other_hostiles:
            other_threat = next(
                (
                    threat
                    for threat in api.assess_threats(entities, state)
                    if threat.entity.get("id") != target_id
                    and threat.distance <= api.MULTI_THREAT_ABORT_RADIUS
                ),
                None,
            )
            if other_threat is not None:
                print(
                    f"Combat aborted: {other_threat.entity.get('type')} entered "
                    f"{other_threat.distance:.1f}m safety radius"
                )
                client.transport.dispatch("chat", {"message": "#stop"})
                client.transport.dispatch("cancel", {})
                return finish("secondary_hostile")

        if target is None:
            # Absence can mean death, despawn, escape, or an unloaded chunk.
            return finish("target_unobserved")
        if not _boss_attack_authorized(client, target, state):
            client.transport.dispatch("cancel", {})
            return finish("boss_not_authorized")
        target_health = target.get("health")
        if target_health is not None and float(target_health) <= 0:
            return finish("verified_health_zero", True)
        distance = target.get("distance", 999)
        if distance >= 7.0 and _is_ranged_target(target, state):
            from .combat_ranged import (
                fire_best_ranged_attack,
                ranged_attack_in_flight,
            )

            if ranged_attack_in_flight(client) or fire_best_ranged_attack(
                client, target
            ):
                time.sleep(0.05)
                continue

        if distance >= 4.5 and not _equip_target_weapon(client, target):
            client.transport.dispatch("cancel", {})
            return finish("weapon_unavailable")

        # Shield any target, including inside 4.5m -- see _prepare_combat_shield.
        shielding["active"] = _prepare_combat_shield(client, target, state, shield)
        if shielding["active"] and navigation_watchdog():
            client.transport.dispatch("cancel", {})
            return finish(str(intervention["reason"] or "shield_unavailable"))

        if distance < 4.5:
            if time.monotonic() < shield["blocked_until"]:
                # The bridge releases use_item on its own.  Wait for that
                # release before attacking so a raised shield cannot suppress
                # the first melee swing after the approach completes.
                time.sleep(0.05)
                continue
            try:
                result = execute_melee_strike(client, target, state)
            except Exception as exc:
                if "entity not found" in str(exc).lower():
                    return finish("target_unobserved")
                print(f"Combat attack failed: {exc}")
                return finish("attack_error")
            if isinstance(result, dict) and result.get("attacked") is False:
                reason = result.get("reason", "unknown")
                if reason == "cooldown":
                    time.sleep(0.05)
                    continue
                print(f"Combat attack declined: {reason}")
                return finish(f"attack_declined:{reason}")
            if state.get("is_pathing", False):
                client.transport.dispatch("chat", {"message": "#stop"})
        else:
            target_position = api.entity_position(target)
            if target_position is None:
                return finish("target_position_missing")
            tx, ty, tz = (
                int(target_position[0]),
                int(target_position[1]),
                int(target_position[2]),
            )
            remaining = max(
                1.0,
                max_duration - (time.time() - started_at),
            )
            approached = api.goto(
                client,
                tx,
                ty,
                tz,
                timeout=min(15, int(remaining)),
                check_interval=0.5,
                tolerance=3.0,
                on_defense=navigation_watchdog,
                defense_check_interval=0.5,
            )
            if approached:
                approach_failures = 0
            else:
                if intervention["reason"] is not None:
                    client.transport.dispatch("cancel", {})
                    return finish(str(intervention["reason"]))
                approach_failures += 1
                if approach_failures >= 3:
                    print(
                        "DEBUG: Baritone failed three bounded target approaches"
                    )
                    client.transport.dispatch("cancel", {})
                    return finish("target_unreachable")
        time.sleep(0.2)

    return finish("combat_timeout")
