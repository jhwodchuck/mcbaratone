import json

from baritone_client.automator.role_profile import RoleProfile, load_role_profile, save_role_profile


def test_loads_legacy_text_profile(tmp_path):
    (tmp_path / "fleet-role.txt").write_text("Nether Supply\n", encoding="utf-8")

    profile = load_role_profile(tmp_path)

    assert profile.primary_role == "nether_supply"
    assert profile.source == "legacy"
    assert profile.valid


def test_loads_complete_json_profile_before_legacy(tmp_path):
    (tmp_path / "fleet-role.txt").write_text("wood_supply", encoding="utf-8")
    (tmp_path / "fleet-role-profile.json").write_text(json.dumps({
        "primary_role": "Builder",
        "secondary_roles": ["farmer"],
        "on_call_roles": ["rescue"],
        "home_anchor": {"x": 10, "z": -4},
        "service_radius": 160,
        "permitted_dimensions": ["minecraft:overworld"],
        "risk_policy": {"retreat_health": 8},
        "reserved_inventory": {"minecraft:bread": 16},
        "production_targets": {"minecraft:oak_log": 64},
        "cooldowns": {"rescue": 30},
        "current_assignment": "repair_village",
        "emergency_responsibilities": ["medic"],
    }), encoding="utf-8")

    profile = load_role_profile(tmp_path)

    assert profile.primary_role == "builder"
    assert profile.supports("FARMER")
    assert profile.home_anchor == {"x": 10, "z": -4}
    assert profile.source == "json"


def test_malformed_profile_recovers_to_legacy_or_default(tmp_path):
    (tmp_path / "fleet-role-profile.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "fleet-role.txt").write_text("scout", encoding="utf-8")
    legacy = load_role_profile(tmp_path)
    assert legacy.primary_role == "scout"
    assert legacy.validation_errors

    (tmp_path / "fleet-role.txt").unlink()
    fallback = load_role_profile(tmp_path, primary_role="Medic")
    assert fallback.primary_role == "medic"
    assert fallback.validation_errors


def test_missing_or_malformed_fields_are_safe_and_explainable(tmp_path):
    (tmp_path / "fleet-role-profile.json").write_text(json.dumps({
        "primary_role": 3, "secondary_roles": "farmer", "service_radius": -1,
        "risk_policy": [], "permitted_dimensions": ["minecraft:the_end", None],
    }), encoding="utf-8")

    profile = load_role_profile(tmp_path, primary_role="balanced")

    assert profile.primary_role == "balanced"
    assert profile.secondary_roles == ()
    assert profile.service_radius is None
    assert profile.risk_policy == {}
    assert profile.permitted_dimensions == ()
    assert profile.validation_errors


def test_parseable_but_invalid_json_falls_back_to_legacy(tmp_path):
    (tmp_path / "fleet-role-profile.json").write_text(
        json.dumps({"primary_role": 3, "secondary_roles": "farmer"}),
        encoding="utf-8",
    )
    (tmp_path / "fleet-role.txt").write_text(
        "quartermaster\n", encoding="utf-8"
    )

    profile = load_role_profile(tmp_path)

    assert profile.primary_role == "quartermaster"
    assert profile.source == "legacy"
    assert profile.validation_errors


def test_deduplicates_roles_and_never_repeats_primary():
    profile = RoleProfile(
        primary_role="Builder", secondary_roles=("builder", "Farmer", "farmer"),
        on_call_roles=("BUILDER", "farmer", "rescue", "rescue"),
    )

    assert profile.secondary_roles == ("farmer",)
    assert profile.on_call_roles == ("farmer", "rescue")
    assert profile.eligible_roles() == ("builder", "farmer", "rescue")


def test_risk_and_operational_data_round_trip(tmp_path):
    original = RoleProfile(
        primary_role="quartermaster", risk_policy={"avoid_lava": True},
        reserved_inventory={"minecraft:golden_carrot": 32}, production_targets={"iron": 128},
        cooldowns={"emergency": 90}, current_assignment={"kind": "supply", "priority": 1},
    )
    save_role_profile(tmp_path, original)

    restored = load_role_profile(tmp_path)

    assert restored.to_dict() == original.to_dict()


def test_reduced_fleet_combined_profile_supports_multiple_specialties(tmp_path):
    profile = RoleProfile(
        primary_role="end_runner", secondary_roles=("nether_supply", "enchanting"),
        on_call_roles=("village_food", "quartermaster"), emergency_responsibilities=("rescue",),
    )
    save_role_profile(tmp_path, profile)

    restored = load_role_profile(tmp_path)

    assert restored.supports("nether-supply")
    assert restored.supports("quartermaster")
    assert not restored.supports("wood_supply")
    assert restored.emergency_responsibilities == ("rescue",)
