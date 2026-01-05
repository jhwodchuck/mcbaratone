import pytest
from unittest.mock import Mock

from baritone_client.utils.arena_loader import ArenaLoader, ArenaDefinition, GameMode
from baritone_client.core.exceptions import ValidationError


class TestArenaLoader:
    """Test cases for ArenaLoader functionality."""

    @pytest.fixture
    def mock_transport(self):
        """Create a mock transport for testing."""
        return Mock()

    @pytest.fixture
    def loader(self, mock_transport):
        """Create ArenaLoader instance with mock transport."""
        return ArenaLoader(mock_transport)

    def test_initialization(self, loader):
        """Test ArenaLoader initialization."""
        assert loader.transport is not None
        assert loader.get_loaded_arena() is None

    def test_load_valid_arena(self, loader):
        """Test loading a valid arena."""
        arena = loader.load("movement_flat", validate_world=False)
        assert isinstance(arena, ArenaDefinition)
        assert arena.name == "movement_flat"
        assert arena.seed == 1001
        assert arena.game_mode == GameMode.SURVIVAL
        assert loader.get_loaded_arena() == arena

    def test_load_invalid_arena(self, loader):
        """Test loading an invalid arena raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            loader.load("nonexistent_arena")

        assert "Unknown arena" in str(exc_info.value)
        assert "nonexistent_arena" in str(exc_info.value)

    def test_list_arenas(self, loader):
        """Test listing all arenas."""
        arenas = loader.list_arenas()
        assert isinstance(arenas, dict)
        assert len(arenas) > 0
        assert "movement_flat" in arenas
        assert all(isinstance(arena, ArenaDefinition) for arena in arenas.values())

    def test_list_survival_arenas(self, loader):
        """Test listing survival mode arenas."""
        arenas = loader.get_survival_arenas()
        assert isinstance(arenas, dict)
        assert all(arena.game_mode == GameMode.SURVIVAL for arena in arenas.values())

    def test_list_creative_arenas(self, loader):
        """Test listing creative mode arenas."""
        arenas = loader.get_creative_arenas()
        assert isinstance(arenas, dict)
        assert all(arena.game_mode == GameMode.CREATIVE for arena in arenas.values())

    def test_list_arenas_by_mode(self, loader):
        """Test listing arenas filtered by game mode."""
        survival_arenas = loader.list_arenas(GameMode.SURVIVAL)
        creative_arenas = loader.list_arenas(GameMode.CREATIVE)

        assert all(arena.game_mode == GameMode.SURVIVAL for arena in survival_arenas.values())
        assert all(arena.game_mode == GameMode.CREATIVE for arena in creative_arenas.values())

    def test_arena_definitions(self, loader):
        """Test that arena definitions have required attributes."""
        arena = loader.load("gap_course", validate_world=False)

        assert arena.name == "gap_course"
        assert arena.seed == 1002
        assert arena.description
        assert isinstance(arena.dimensions, tuple)
        assert len(arena.dimensions) == 3
        assert isinstance(arena.game_mode, GameMode)
        assert isinstance(arena.structures, list)
        assert isinstance(arena.spawn_position, tuple)
        assert len(arena.spawn_position) == 3

    def test_comprehensive_arenas_coverage(self, loader):
        """Test that all expected arena suites are covered."""
        arenas = loader.list_arenas()

        # Check for presence of key suites
        suite_100_arenas = [name for name in arenas.keys() if name.startswith(('movement_flat', 'gap_course', 'vertical_test'))]
        suite_200_arenas = [name for name in arenas.keys() if name.startswith(('mining_test', 'building_area', 'house_interior'))]
        suite_300_arenas = [name for name in arenas.keys() if name.startswith(('item_scatter', 'inventory_test', 'gear_room'))]
        suite_400_arenas = [name for name in arenas.keys() if name.startswith(('crafting_station', 'workbench', 'smelting_setup'))]
        suite_500_arenas = [name for name in arenas.keys() if name.startswith(('combat_ring', 'jump_attack', 'defense_test'))]
        suite_600_arenas = [name for name in arenas.keys() if name.startswith(('scan_test', 'entity_field', 'structure_test'))]
        suite_700_arenas = [name for name in arenas.keys() if name.startswith(('portal_site', 'portal_ready', 'active_portal'))]
        suite_800_arenas = [name for name in arenas.keys() if name.startswith(('stronghold_end', 'active_end_portal', 'end_combat'))]
        suite_900_arenas = [name for name in arenas.keys() if name.startswith(('spawn_area', 'mining_area', 'stronghold_start'))]

        # Ensure we have representatives from each suite
        assert len(suite_100_arenas) > 0, "Suite 100 (Movement) arenas should be present"
        assert len(suite_200_arenas) > 0, "Suite 200 (Block Interaction) arenas should be present"
        assert len(suite_300_arenas) > 0, "Suite 300 (Inventory) arenas should be present"
        assert len(suite_400_arenas) > 0, "Suite 400 (Crafting) arenas should be present"
        assert len(suite_500_arenas) > 0, "Suite 500 (Combat) arenas should be present"
        assert len(suite_600_arenas) > 0, "Suite 600 (Sensing) arenas should be present"
        assert len(suite_700_arenas) > 0, "Suite 700 (Travel) arenas should be present"
        assert len(suite_800_arenas) > 0, "Suite 800 (Endgame) arenas should be present"
        assert len(suite_900_arenas) > 0, "Suite 900 (Integration) arenas should be present"

    def test_game_mode_enum_values(self):
        """Test GameMode enum values."""
        assert GameMode.SURVIVAL.value == "survival"
        assert GameMode.CREATIVE.value == "creative"
        assert GameMode.ADVENTURE.value == "adventure"
        assert GameMode.SPECTATOR.value == "spectator"

    def test_validation_disabled(self, loader):
        """Test that validation can be disabled."""
        arena = loader.load("movement_flat", validate_world=False)
        assert arena is not None
        # Should not raise any exceptions since validation is disabled

    @pytest.mark.parametrize("arena_name,expected_seed", [
        ("movement_flat", 1001),
        ("gap_course", 1002),
        ("vertical_test", 1003),
        ("mining_test", 2001),
        ("building_area", 2002),
        ("combat_ring", 5001),
        ("spawn_area", 9001),
        ("fresh_world", 9999),
    ])
    def test_parametrized_arenas(self, loader, arena_name, expected_seed):
        """Test multiple arenas with parametrized test."""
        arena = loader.load(arena_name, validate_world=False)
        assert arena.seed == expected_seed
        assert arena.name == arena_name