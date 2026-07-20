"""
Tests for State Versioning System.
"""

import pytest
import json
import tempfile
from pathlib import Path

from baritone_client.automator.versioning import SemanticVersion, VersionManager
from baritone_client.automator.migration import MigrationManager, create_default_checkpoint_migration_registry
from baritone_client.automator.serialization_multi import FileSerializer, SerializationFormat
from baritone_client.automator.storage import create_filesystem_storage
from baritone_client.automator.state_manager import StateManager, Phase


class TestSemanticVersion:
    """Test SemanticVersion functionality."""

    def test_version_parsing(self):
        """Test version string parsing."""
        v = SemanticVersion.parse("1.2.3")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3
        assert v.prerelease is None
        assert v.build is None

    def test_version_with_prerelease(self):
        """Test version with prerelease."""
        v = SemanticVersion.parse("2.0.0-alpha.1")
        assert v.major == 2
        assert v.minor == 0
        assert v.patch == 0
        assert v.prerelease == "alpha.1"

    def test_version_string_representation(self):
        """Test version string representation."""
        v = SemanticVersion(1, 2, 3, "beta", "build.123")
        assert str(v) == "1.2.3-beta+build.123"

    def test_version_comparison(self):
        """Test version comparison."""
        v1 = SemanticVersion.parse("1.0.0")
        v2 = SemanticVersion.parse("1.1.0")
        v3 = SemanticVersion.parse("2.0.0")

        assert v1 < v2
        assert v2 < v3
        assert v1 < v3
        assert v3 > v2
        assert v2 > v1

    def test_version_compatibility(self):
        """Test version compatibility checking."""
        v1 = SemanticVersion.parse("1.0.0")
        v2 = SemanticVersion.parse("1.5.0")
        v3 = SemanticVersion.parse("2.0.0")

        assert v1.is_compatible(v2)
        assert not v1.is_compatible(v3)
        assert v2.is_compatible(v1)

    def test_breaking_change_detection(self):
        """Test breaking change detection."""
        v1 = SemanticVersion.parse("1.0.0")
        v2 = SemanticVersion.parse("2.0.0")

        assert v2.is_breaking_change(v1)
        assert not v1.is_breaking_change(v2)


class TestVersionManager:
    """Test VersionManager functionality."""

    def test_current_version(self):
        """Test getting current version."""
        vm = VersionManager()
        current = vm.get_current_version()
        assert isinstance(current, SemanticVersion)
        assert str(current) == "1.0.0"

    def test_version_validation(self):
        """Test version validation."""
        vm = VersionManager()

        valid_version = SemanticVersion(1, 2, 3)
        assert vm.validate_version(valid_version)

        invalid_version = SemanticVersion(-1, 0, 0)
        assert not vm.validate_version(invalid_version)


class TestMigrationManager:
    """Test MigrationManager functionality."""

    def test_checkpoint_migration(self):
        """Test checkpoint migration."""
        registry = create_default_checkpoint_migration_registry()
        manager = MigrationManager(registry)

        # Test data for version 1.0.0
        old_data = {
            "phase": "BOOT_SEQUENCE",
            "position": [0, 64, 0],
            "inventory_summary": {"wood": 10},
            "timestamp": 1234567890,
            "schema_version": "1.0.0"
        }

        from_version = SemanticVersion.parse("1.0.0")
        to_version = SemanticVersion.parse("1.1.0")

        result = manager.migrate_checkpoint(old_data, from_version, to_version)

        assert result.success
        assert "phase_payloads" in result.data
        assert result.data["schema_version"] == "1.1.0"

    def test_migration_rollback(self):
        """Test migration rollback."""
        registry = create_default_checkpoint_migration_registry()
        manager = MigrationManager(registry)

        # Migration requires phase/position/timestamp, like every real checkpoint
        original_data = {
            "phase": "BOOT_SEQUENCE",
            "position": [0, 64, 0],
            "timestamp": 1234567890,
            "schema_version": "1.0.0",
        }

        # Perform migration
        result = manager.migrate_checkpoint(
            original_data,
            SemanticVersion.parse("1.0.0"),
            SemanticVersion.parse("1.1.0")
        )
        assert result.success

        # Test rollback
        assert manager.can_rollback()
        rolled_back = manager.rollback_checkpoint()
        assert rolled_back is not None
        assert rolled_back["schema_version"] == "1.0.0"


class TestMultiFormatSerialization:
    """Test multi-format serialization."""

    def test_json_serialization(self):
        """Test JSON serialization."""
        serializer = FileSerializer()

        data = {"test": "data", "number": 42}
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.json"
            serializer.save_to_file(data, filepath, format=SerializationFormat.JSON)

            loaded = serializer.load_from_file(filepath)
            assert loaded == data

    def test_compressed_serialization(self):
        """Test compressed serialization."""
        serializer = FileSerializer()

        data = {"test": "data", "large_field": "x" * 1000}
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.json.gz"
            serializer.save_to_file(data, filepath, format=SerializationFormat.JSON_GZ)

            loaded = serializer.load_from_file(filepath)
            assert loaded == data


class TestDistributedStorage:
    """Test distributed storage functionality."""

    def test_filesystem_storage(self):
        """Test filesystem storage (async API driven synchronously via asyncio.run)."""
        import asyncio

        async def scenario():
            with tempfile.TemporaryDirectory() as tmpdir:
                storage = create_filesystem_storage(tmpdir)

                test_data = b"test data"
                key = "test_key"

                # Store data
                from baritone_client.automator.storage import StorageMetadata
                import time
                metadata = StorageMetadata(
                    key=key,
                    size=len(test_data),
                    checksum="",
                    created_at=time.time(),
                    modified_at=time.time()
                )

                await storage.store(key, test_data, metadata)

                # Retrieve data
                retrieved = await storage.retrieve(key)
                assert retrieved == test_data

                # Check existence
                assert await storage.exists(key)

        asyncio.run(scenario())


class TestStateManagerIntegration:
    """Test StateManager with new versioning system."""

    def test_backward_compatibility(self):
        """Test loading legacy checkpoints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create legacy checkpoint
            legacy_data = {
                "phase": "BOOT_SEQUENCE",
                "position": [0, 64, 0],
                "inventory_summary": {"wood": 10},
                "timestamp": 1234567890.0,
                "phase_progress": {"BOOT_SEQUENCE": 0.5},
                "custom_data": {},
                "phase_payloads": {},
                "world_seed": 12345
            }

            filepath = Path(tmpdir) / StateManager.CHECKPOINT_FILE
            with open(filepath, 'w') as f:
                json.dump(legacy_data, f)

            # Load with new StateManager
            state_manager = StateManager(checkpoint_dir=tmpdir)
            loaded = state_manager.load_checkpoint()

            assert loaded
            assert state_manager.current_phase == Phase.BOOT_SEQUENCE
            assert state_manager._last_position == (0, 64, 0)

    def test_versioned_checkpoint_save_load(self):
        """Test saving and loading versioned checkpoints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_manager = StateManager(checkpoint_dir=tmpdir)

            # Modify state (don't hardcode the enum order - it grows over time)
            state_manager.advance_phase()
            state_manager.update_progress(0.75)
            expected_phase = state_manager.get_current_phase()

            # Save checkpoint
            inventory = {"diamond": 5, "iron": 20}
            filepath = state_manager.save_checkpoint(inventory)

            # Create new StateManager and load
            new_state_manager = StateManager(checkpoint_dir=tmpdir)
            loaded = new_state_manager.load_checkpoint()

            assert loaded
            assert new_state_manager.current_phase == expected_phase
            assert new_state_manager.get_progress() == 0.75

    def test_checkpoint_validation(self):
        """Test checkpoint validation."""
        # Isolated dir: a default StateManager() points at the repo root and
        # can touch the live bot's checkpoint.
        with tempfile.TemporaryDirectory() as tmpdir:
            state_manager = StateManager(checkpoint_dir=tmpdir)

            # Valid state should pass validation
            report = state_manager.validate_checkpoint_integrity()
            assert report['valid']

            # Test with invalid state
            state_manager.current_phase = "invalid_phase"  # Invalid type
            report = state_manager.validate_checkpoint_integrity()
            assert not report['valid']
            assert len(report['errors']) > 0

    def test_checkpoint_info(self):
        """Test checkpoint info retrieval."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_manager = StateManager(checkpoint_dir=tmpdir)

            info = state_manager.get_checkpoint_info()
            assert 'current_phase' in info
            assert 'schema_version' in info
            assert 'overall_progress' in info
            assert 'storage_type' in info


if __name__ == "__main__":
    pytest.main([__file__])