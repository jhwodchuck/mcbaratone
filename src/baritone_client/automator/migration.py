"""
Migration System for State Management.

Provides version-to-version transformation capabilities for checkpoint data migration.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Callable, Optional, List, TypeVar, Generic
from dataclasses import dataclass

from .versioning import SemanticVersion

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class MigrationResult:
    """Result of a migration operation."""
    success: bool
    data: Dict[str, Any]
    errors: List[str]
    warnings: List[str]


class MigrationError(Exception):
    """Exception raised when migration fails."""
    pass


class MigrationRollbackError(Exception):
    """Exception raised when migration rollback fails."""
    pass


class MigrationStrategy(ABC, Generic[T]):
    """Abstract base class for migration strategies."""

    @abstractmethod
    def migrate(self, data: T, from_version: SemanticVersion, to_version: SemanticVersion) -> MigrationResult:
        """
        Migrate data from one version to another.

        Args:
            data: The data to migrate
            from_version: Source version
            to_version: Target version

        Returns:
            MigrationResult with migrated data and any errors/warnings
        """
        pass

    @abstractmethod
    def can_migrate(self, from_version: SemanticVersion, to_version: SemanticVersion) -> bool:
        """
        Check if this strategy can handle the given version migration.

        Args:
            from_version: Source version
            to_version: Target version

        Returns:
            True if this strategy can handle the migration
        """
        pass


class CheckpointMigrationStrategy(MigrationStrategy[Dict[str, Any]]):
    """Migration strategy for checkpoint data."""

    def __init__(self):
        """Initialize checkpoint migration strategy."""
        self._transformers: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}

    def register_transformer(self, version_key: str, transformer: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        """
        Register a transformation function for a specific version.

        Args:
            version_key: Version identifier (e.g., "1.0.0_to_1.1.0")
            transformer: Function that transforms data
        """
        self._transformers[version_key] = transformer

    def can_migrate(self, from_version: SemanticVersion, to_version: SemanticVersion) -> bool:
        """Check if migration between versions is supported."""
        version_key = f"{from_version}_to_{to_version}"
        return version_key in self._transformers

    def migrate(self, data: Dict[str, Any], from_version: SemanticVersion, to_version: SemanticVersion) -> MigrationResult:
        """Migrate checkpoint data."""
        errors = []
        warnings = []

        try:
            version_key = f"{from_version}_to_{to_version}"
            if version_key not in self._transformers:
                errors.append(f"No migration path from {from_version} to {to_version}")
                return MigrationResult(False, data, errors, warnings)

            # Apply transformation
            transformed_data = self._transformers[version_key](data.copy())

            # Validate transformed data has required fields
            required_fields = ["phase", "position", "timestamp"]
            for field in required_fields:
                if field not in transformed_data:
                    errors.append(f"Required field '{field}' missing after migration")
                    return MigrationResult(False, transformed_data, errors, warnings)

            return MigrationResult(True, transformed_data, errors, warnings)

        except Exception as e:
            errors.append(f"Migration failed: {str(e)}")
            logger.exception("Migration error")
            return MigrationResult(False, data, errors, warnings)


class MigrationRegistry:
    """Registry for migration strategies and version mappings."""

    def __init__(self):
        """Initialize migration registry."""
        self._strategies: List[MigrationStrategy] = []
        self._version_map: Dict[str, SemanticVersion] = {}

    def register_strategy(self, strategy: MigrationStrategy) -> None:
        """
        Register a migration strategy.

        Args:
            strategy: Migration strategy to register
        """
        self._strategies.append(strategy)

    def find_migration_path(self, from_version: SemanticVersion, to_version: SemanticVersion) -> Optional[MigrationStrategy]:
        """
        Find a strategy that can migrate between the given versions.

        Args:
            from_version: Source version
            to_version: Target version

        Returns:
            Migration strategy if found, None otherwise
        """
        for strategy in self._strategies:
            if strategy.can_migrate(from_version, to_version):
                return strategy
        return None

    def migrate_data(self, data: Dict[str, Any], from_version: SemanticVersion, to_version: SemanticVersion) -> MigrationResult:
        """
        Migrate data using available strategies.

        Args:
            data: Data to migrate
            from_version: Source version
            to_version: Target version

        Returns:
            Migration result
        """
        strategy = self.find_migration_path(from_version, to_version)
        if not strategy:
            return MigrationResult(
                False,
                data,
                [f"No migration strategy found from {from_version} to {to_version}"],
                []
            )

        return strategy.migrate(data, from_version, to_version)

    def get_supported_versions(self) -> List[SemanticVersion]:
        """Get list of all supported versions across registered strategies."""
        versions = set()
        # This would need to be implemented based on strategy capabilities
        # For now, return empty list
        return list(versions)


class MigrationManager:
    """High-level manager for data migrations with rollback support."""

    def __init__(self, registry: Optional[MigrationRegistry] = None):
        """
        Initialize migration manager.

        Args:
            registry: Migration registry to use (creates default if None)
        """
        self._registry = registry or MigrationRegistry()
        self._migration_history: List[Dict[str, Any]] = []

    def migrate_checkpoint(self, checkpoint_data: Dict[str, Any],
                          from_version: SemanticVersion,
                          to_version: SemanticVersion) -> MigrationResult:
        """
        Migrate checkpoint data with history tracking.

        Args:
            checkpoint_data: Checkpoint data to migrate
            from_version: Source version
            to_version: Target version

        Returns:
            Migration result
        """
        # Store original state for potential rollback
        original_state = {
            'data': checkpoint_data.copy(),
            'from_version': from_version,
            'to_version': to_version,
            'timestamp': __import__('time').time()
        }

        result = self._registry.migrate_data(checkpoint_data, from_version, to_version)

        if result.success:
            # Record successful migration
            self._migration_history.append({
                **original_state,
                'success': True,
                'migrated_data': result.data.copy()
            })
        else:
            # Record failed migration
            self._migration_history.append({
                **original_state,
                'success': False,
                'errors': result.errors,
                'warnings': result.warnings
            })

        return result

    def can_rollback(self, steps: int = 1) -> bool:
        """
        Check if rollback is possible.

        Args:
            steps: Number of steps to rollback

        Returns:
            True if rollback is possible
        """
        return len(self._migration_history) >= steps

    def rollback_checkpoint(self, steps: int = 1) -> Optional[Dict[str, Any]]:
        """
        Rollback checkpoint to previous state.

        Args:
            steps: Number of migration steps to rollback

        Returns:
            Original checkpoint data if rollback successful, None otherwise
        """
        if not self.can_rollback(steps):
            raise MigrationRollbackError(f"Cannot rollback {steps} steps, only {len(self._migration_history)} available")

        try:
            # Get the state to rollback to
            target_state = self._migration_history[-steps]

            # Remove the rolled back migrations from history
            self._migration_history = self._migration_history[:-steps]

            return target_state['data']

        except (IndexError, KeyError) as e:
            raise MigrationRollbackError(f"Rollback failed: {str(e)}") from e

    def get_migration_history(self) -> List[Dict[str, Any]]:
        """Get migration history."""
        return self._migration_history.copy()

    def clear_history(self) -> None:
        """Clear migration history."""
        self._migration_history.clear()


# Pre-configured migration strategies for common transformations

def migrate_checkpoint_1_0_0_to_1_1_0(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Migrate checkpoint from version 1.0.0 to 1.1.0.

    Changes:
    - Add new 'phase_payloads' field if missing
    - Ensure 'custom_data' is properly structured
    """
    migrated = data.copy()

    # Add phase_payloads if missing
    if 'phase_payloads' not in migrated:
        migrated['phase_payloads'] = {}

    # Ensure custom_data exists
    if 'custom_data' not in migrated:
        migrated['custom_data'] = {}

    # Update version metadata
    migrated['schema_version'] = '1.1.0'

    return migrated


def migrate_checkpoint_1_1_0_to_2_0_0(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Migrate checkpoint from version 1.1.0 to 2.0.0.

    Changes:
    - Restructure phase progress tracking
    - Add validation metadata
    """
    migrated = data.copy()

    # Restructure phase_progress if it exists
    if 'phase_progress' in migrated:
        phase_progress = migrated['phase_progress']
        # Ensure all phases have progress values
        for phase_name in phase_progress.keys():
            if not isinstance(phase_progress[phase_name], (int, float)):
                phase_progress[phase_name] = 0.0

    # Add validation metadata
    migrated['validation'] = {
        'checksum': hash(str(migrated.get('custom_data', {}))),
        'validated_at': __import__('time').time()
    }

    # Update version metadata
    migrated['schema_version'] = '2.0.0'

    return migrated


def create_default_checkpoint_migration_registry() -> MigrationRegistry:
    """Create a registry with default checkpoint migration strategies."""
    registry = MigrationRegistry()
    strategy = CheckpointMigrationStrategy()

    # Register migration transformers
    strategy.register_transformer("1.0.0_to_1.1.0", migrate_checkpoint_1_0_0_to_1_1_0)
    strategy.register_transformer("1.1.0_to_2.0.0", migrate_checkpoint_1_1_0_to_2_0_0)

    registry.register_strategy(strategy)
    return registry