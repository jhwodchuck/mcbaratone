"""Supervised melee execution behind :func:`combat.safe_combat`."""

import time

from . import combat as api


SHIELD_HOLD_MS = 1800
SHIELD_REFRESH_SECONDS = 2.0


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
    """Equip and verify a carried shield for a forced ranged approach."""
    try:
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
    api.equip_best_weapon(client)
    intervention = {"reason": None}
    ranged_approach = {"active": False}
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

    def navigation_watchdog() -> bool:
        state = client.transport.dispatch("get_state", {})
        api.ensure_alive(client, state)
        health = float(state.get("health", 20.0) or 0)
        if not no_retreat and health < retreat_health:
            intervention["reason"] = "retreat_health"
            return True
        now = time.monotonic()
        if (
            ranged_approach["active"]
            and shield["ready"]
            and now >= shield["refresh_at"]
        ):
            client.transport.dispatch(
                "use_item", {"duration_ms": SHIELD_HOLD_MS}
            )
            shield["blocked_until"] = now + SHIELD_HOLD_MS / 1000.0
            shield["refresh_at"] = now + SHIELD_REFRESH_SECONDS
        return False

    started_at = time.time()
    approach_failures = 0
    while time.time() - started_at < max_duration:
        snapshot = api._get_combat_snapshot(
            client,
            radius=max(30, tracking_radius),
        )
        if snapshot is not None:
            state = snapshot["player"]
            entities = snapshot["entities"]
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
        target_health = target.get("health")
        if target_health is not None and float(target_health) <= 0:
            return finish("verified_health_zero", True)

        distance = target.get("distance", 999)
        if distance < 4.5:
            ranged_approach["active"] = False
            if time.monotonic() < shield["blocked_until"]:
                # The bridge releases use_item on its own.  Wait for that
                # release before attacking so a raised shield cannot suppress
                # the first melee swing after the approach completes.
                time.sleep(0.05)
                continue
            api.look_at_entity(client, target)
            cooldown = api._attack_cooldown(state)
            if cooldown < api.MELEE_ATTACK_COOLDOWN_THRESHOLD:
                time.sleep(0.05)
                continue
            try:
                result = client.transport.dispatch(
                    "attack_entity",
                    {
                        "entity_id": target_id,
                        "min_cooldown": api.MELEE_ATTACK_COOLDOWN_THRESHOLD,
                    },
                )
                api.combat_telemetry.record_melee_attack(client, target, result)
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
            ranged_approach["active"] = _is_ranged_target(target, state)
            if ranged_approach["active"] and not shield["checked"]:
                shield["ready"] = _prepare_shield(client)
                shield["checked"] = True
            if ranged_approach["active"] and shield["ready"]:
                navigation_watchdog()
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
