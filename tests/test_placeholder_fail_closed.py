from baritone_client.automator.phases.iron_farm import IronFarmHandler
from baritone_client.automator.phases.villager import VillagerInfraHandler
from baritone_client.automator.phases.xp_engine import XpEngineHandler
from baritone_client.common import iron_farm, mob_farm, villager


def test_unimplemented_common_helpers_fail_closed():
    client = object()
    assert not mob_farm.build_simple_mob_farm(client, 0, 64, 0)
    assert not mob_farm.grind_xp_at_location(client, 0, 64, 0)
    assert not mob_farm.enchant_tool_perfectly(client, 0, ["fortune"])
    assert not villager.capture_villager(client, 2)
    assert not villager.build_villager_breeder(client, 0, 64, 0)
    assert not villager.lock_librarian(client, "mending")
    assert not villager.start_villager_multiplication(client)
    assert not iron_farm.build_iron_farm(client, 0, 64, 0)
    assert not iron_farm.move_villagers_to_farm(client, [], (0, 64, 0))
    assert not iron_farm.add_zombie_to_farm(client, (0, 64, 0))
    assert not iron_farm.start_iron_production(client, (0, 64, 0))


def test_unimplemented_phase_scaffolds_fail_closed():
    client = object()
    assert not XpEngineHandler()._setup_xp_farm(client)
    assert not XpEngineHandler()._grind_xp(client)
    assert not XpEngineHandler()._enchant_tools(client)
    assert not VillagerInfraHandler()._capture_villagers(client)
    assert not VillagerInfraHandler()._build_breeder(client)
    assert not VillagerInfraHandler()._lock_librarian(client)
    assert not VillagerInfraHandler()._start_breeding(client)
    assert not IronFarmHandler()._move_villagers(client, (0, 64, 0))
    assert not IronFarmHandler()._add_zombie(client, (0, 64, 0))
    assert not IronFarmHandler()._build_iron_farm(client, (0, 64, 0))
    assert not IronFarmHandler()._start_production(client, (0, 64, 0))
