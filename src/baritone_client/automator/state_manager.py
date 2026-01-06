"""
State Manager - Handles phase progression and checkpoint persistence with versioning and migration support.
"""

import json
import os
import logging
from enum import Enum, auto
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Union, List
from pathlib import Path

from .versioning import VersionManager, SemanticVersion
from .migration import MigrationManager, create_default_checkpoint_migration_registry
from .serialization_multi import FileSerializer, SerializationFormat
from .storage import DistributedStorageManager, create_filesystem_storage, StorageMetadata

logger = logging.getLogger(__name__)


class Phase(Enum):
    """Phases of end-game progression."""
    BRIDGE_CHECK = auto()       # Verify bridge connectivity and APIs
    SPAWN_BOOTSTRAP = auto()    # Phase 1: Initial spawn setup
    INITIAL_GATHERING = auto()  # Phase 2: Gather basic resources
    BASE_CONSTRUCTION = auto()  # Phase 3: Build initial base
    BOOT_SEQUENCE = auto()      # Phase 4: Shelter, Food, Tools
    FOOD_AND_IRON = auto()      # Phase 5: Food independence, Iron gear
    ENCHANTING_PIPELINE = auto()# Phase 6: Level 30 enchanting
    NETHER_AND_BLAZE = auto()   # Phase 7: Blaze rods
    VILLAGER_INFRA = auto()     # Phase 8: Villager breeder operational
    XP_ENGINE = auto()          # Phase 9: Infinite XP
    IRON_FARM = auto()          # Phase 10: Infinite iron
    TOOL_PERFECTION = auto()    # Phase 11: Mending economy
    WORLD_UNLOCK = auto()       # Phase 12: End access and shulker boxes
    MEGABASE_INIT = auto()      # Phase 13: Megabase initialization
    COMPLETE = auto()           # Victory!


@dataclass
class Checkpoint:
    """Checkpoint data for resumption."""
    phase: Phase
    position: tuple[float, float, float]
    inventory_summary: Dict[str, int]
    timestamp: float
    custom_data: Dict[str, Any]


@dataclass
class PhaseCondition:
    """
    Weighted condition for phase transition.
    
    Attributes:
        name: Identifier for the condition
        description: Human readable description
        weight: Importance (0.0 to 1.0, or relative)
        is_optional: If true, failure doesn't block transition (just affects score)
        check_fn: Optional callable returning (bool, float_score)
    """
    name: str
    description: str
    weight: float = 1.0
    is_optional: bool = False
    
    def evaluate(self, context: Any) -> float:
        """
        Evaluate condition against context.
        Returns weighted score (0.0 to weight).
        """
        # Checks would need to be injected or registered.
        # For simple data carrying, we might rely on the caller to check value.
        return 0.0


class StateManager:
    """
    Manages phase progression and checkpoint persistence with versioning and migration support.

    Provides:
    - Current phase tracking
    - Phase advancement with validation
    - Checkpoint save/load for crash recovery with automatic migration
    - Progress percentage per phase
    - Schema versioning and backward compatibility
    - Multi-format serialization support
    - Distributed storage abstraction
    """

    CHECKPOINT_FILE = "spawn_to_dragon_checkpoint.json"
    LEGACY_CHECKPOINT_VERSION = SemanticVersion(0, 0, 0)  # Version for unversioned checkpoints


    def __init__(self,
                 checkpoint_dir: Optional[Union[str, Path]] = None,
                 storage_manager: Optional[DistributedStorageManager] = None,
                 migration_manager: Optional[MigrationManager] = None,
                 serializer: Optional[FileSerializer] = None,
                 serialization_format: SerializationFormat = SerializationFormat.JSON):
        """
        Initialize state manager with versioning and migration support.

        Args:
            checkpoint_dir: Directory for checkpoint files (backward compatibility)
            storage_manager: Distributed storage manager for high availability
            migration_manager: Migration manager for schema upgrades
            serializer: Multi-format serializer
            serialization_format: Default serialization format
        """
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else Path.cwd()

        # Initialize versioning system
        self.version_manager = VersionManager()

        # Initialize storage system
        self.storage_manager = storage_manager or create_filesystem_storage(self.checkpoint_dir)

        # Initialize migration system
        registry = create_default_checkpoint_migration_registry()
        self.migration_manager = migration_manager or MigrationManager(registry)

        # Initialize serialization system
        self.serializer = serializer or FileSerializer()
        self.serialization_format = serialization_format

        # Core state
        self.current_phase = Phase.BRIDGE_CHECK
        self.phase_progress: Dict[Phase, float] = {p: 0.0 for p in Phase}
        self.custom_data: Dict[str, Any] = {}
        self.phase_payloads: Dict[str, Dict[str, Any]] = {}
        self._last_position: tuple[float, float, float] = (0, 64, 0)

        # Phase Readiness Scores
        self.phase_readiness: Dict[Phase, float] = {}

        # Phase conditions placeholder (to be implemented later)
        self.phase_conditions: Dict[Phase, Any] = {}

        # Versioning state
        self.current_schema_version = self.version_manager.get_current_version()
        self.loaded_schema_version: Optional[SemanticVersion] = None

    def check_transition(self, phase: Phase, context: Any = None) -> bool:
        """
        Check if we should transition from the given phase.
        Uses fuzzy capability: if readiness > threshold (e.g. 1.0), returns True.
        """
        conditions = self.phase_conditions.get(phase, [])
        if not conditions:
            # Fallback to manual transition signals if no conditions defined
            return False
            
        total_score = 0.0
        max_possible = sum(c.weight for c in conditions)
        
        for condition in conditions:
            # In a real impl, we'd pass context to evaluate.
            # Here we assume evaluate returns the accumulated score.
            # But PhaseCondition.evaluate returns 0.0 by default in my stub.
            # This logic connects the pieces.
            score = condition.evaluate(context)
            total_score += score
            
        readiness = total_score / max_possible if max_possible > 0 else 0.0
        self.phase_readiness[phase] = readiness
        
        return readiness >= 1.0
        
    def get_current_phase(self) -> Phase:
        """Get the current automation phase."""
        return self.current_phase
    
    def set_phase(self, phase: Phase) -> None:
        """Set the current phase directly."""
        self.current_phase = phase
        
    def advance_phase(self) -> bool:
        """
        Advance to the next phase.
        
        Returns:
            True if advanced, False if already at COMPLETE
        """
        phases = list(Phase)
        current_idx = phases.index(self.current_phase)
        
        if current_idx < len(phases) - 1:
            self.current_phase = phases[current_idx + 1]
            self.phase_progress[self.current_phase] = 0.0
            return True
        return False
    
    def update_progress(self, progress: float) -> None:
        """
        Update progress for current phase.
        
        Args:
            progress: Progress percentage (0.0 to 1.0)
        """
        self.phase_progress[self.current_phase] = max(0.0, min(1.0, progress))
    
    def get_progress(self, phase: Optional[Phase] = None) -> float:
        """Get progress for a phase (default: current)."""
        return self.phase_progress.get(phase or self.current_phase, 0.0)
    
    def update_position(self, x: float, y: float, z: float) -> None:
        """Update last known position."""
        self._last_position = (x, y, z)
    
    def record_phase_payload(self, phase: Phase, payload: Dict[str, Any]) -> None:
        """Persist payload for phase to custom data."""
        if not payload:
            return
        self.phase_payloads[phase.name] = payload
        self.custom_data.setdefault("phase_payloads", {}).update({phase.name: payload})
    
    def add_location(self, category: str, x: int, y: int, z: int, dimension: str = "overworld", tags: Optional[list] = None, client=None) -> None:
        """
        Record a location of interest and optionally create a Baritone waypoint.
        
        Args:
            category: Type of location (e.g. 'chest', 'bed', 'portal')
            x, y, z: Coordinates
            dimension: Dimension name
            tags: Optional tags
            client: Optional client instance to sync waypoint to Baritone
        """
        locations = self.custom_data.setdefault("locations", {})
        category_list = locations.setdefault(category, [])
        
        # Avoid duplicates close to each other
        for loc in category_list:
            if loc["dimension"] == dimension and abs(loc["x"]-x) < 3 and abs(loc["y"]-y) < 3 and abs(loc["z"]-z) < 3:
                return # Already recorded
                
        category_list.append({
            "x": x, "y": y, "z": z,
            "dimension": dimension,
            "tags": tags or [],
            "timestamp": __import__("time").time()
        })
        
        if client:
            try:
                # Sync to Baritone Waypoints
                # Usage: #waypoint save <tag> <x> <y> <z>
                tag = f"{category}_{len(category_list)}"
                client.transport.dispatch("chat", {"message": f"#waypoint save {tag} {x} {y} {z}"})
            except Exception as e:
                print(f"Failed to sync waypoint: {e}")
        
    def get_locations(self, category: str = None) -> Dict[str, list]:
        """Get recorded locations (filtered by category if provided)."""
        locs = self.custom_data.get("locations", {})
        if category:
            return {category: locs.get(category, [])}
        return locs
    
    def get_phase_payload(self, phase: Phase) -> Dict[str, Any]:
        """Retrieve stored payload for a phase."""
        return self.phase_payloads.get(phase.name, {})
    
    def save_checkpoint(self, inventory_summary: Dict[str, int], world_seed: Optional[int] = None) -> str:
        """
        Save current state to checkpoint file with versioning support.

        Args:
            inventory_summary: Dict of item_id -> count
            world_seed: Optional world seed to store

        Returns:
            Path to saved checkpoint file
        """
        import time
        import asyncio

        # Create checkpoint data with version metadata
        checkpoint = {
            "phase": self.current_phase.name,
            "position": list(self._last_position),
            "inventory_summary": inventory_summary,
            "timestamp": time.time(),
            "phase_progress": {p.name: v for p, v in self.phase_progress.items()},
            "custom_data": self.custom_data,
            "phase_payloads": self.phase_payloads,
            "world_seed": world_seed,
            "schema_version": str(self.current_schema_version)
        }

        # Use distributed storage system
        try:
            # Run async storage operation in event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def save_async():
                # Create metadata for storage
                metadata = StorageMetadata(
                    key=self.CHECKPOINT_FILE,
                    size=0,  # Will be calculated by serializer
                    checksum="",  # Will be calculated by storage
                    created_at=time.time(),
                    modified_at=time.time(),
                    version=str(self.current_schema_version),
                    tags={"type": "checkpoint", "phase": self.current_phase.name}
                )

                # Save using file serializer with versioning
                filepath = self.checkpoint_dir / self.CHECKPOINT_FILE
                self.serializer.save_to_file(
                    checkpoint,
                    filepath,
                    format=self.serialization_format,
                    add_metadata=True
                )

                # Also store in distributed storage for high availability
                checkpoint_bytes = self.serializer._serializer.serialize(checkpoint, self.serialization_format)
                await self.storage_manager.store(
                    self.CHECKPOINT_FILE,
                    checkpoint_bytes,
                    metadata
                )

                return str(filepath)

            filepath = loop.run_until_complete(save_async())
            loop.close()

            logger.info(f"Checkpoint saved with version {self.current_schema_version} to {filepath}")
            return filepath

        except Exception as e:
            logger.error(f"Failed to save checkpoint: {str(e)}")
            # Fallback to legacy method for backward compatibility
            return self._save_checkpoint_legacy(checkpoint)

    def _save_checkpoint_legacy(self, checkpoint: Dict[str, Any]) -> str:
        """
        Legacy checkpoint saving method for backward compatibility.

        Args:
            checkpoint: Checkpoint data dictionary

        Returns:
            Path to saved checkpoint file
        """
        filepath = self.checkpoint_dir / self.CHECKPOINT_FILE
        with open(filepath, "w") as f:
            json.dump(checkpoint, f, indent=2)
        return str(filepath)
    
    def load_checkpoint(self, current_seed: Optional[int] = None) -> bool:
        """
        Load state from checkpoint file with automatic migration support.

        Args:
            current_seed: Optional current world seed to verify against

        Returns:
            True if checkpoint loaded, False if not found or seed mismatch
        """
        import asyncio

        try:
            # Try new versioning system first
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def load_async():
                # Try distributed storage first, fallback to filesystem
                try:
                    checkpoint_bytes = await self.storage_manager.retrieve(self.CHECKPOINT_FILE)
                    checkpoint_data = self.serializer._serializer.deserialize(checkpoint_bytes)
                except Exception:
                    # Fallback to file-based loading
                    filepath = self.checkpoint_dir / self.CHECKPOINT_FILE
                    if not filepath.exists():
                        return False
                    checkpoint_data = self.serializer.load_from_file(filepath)

                return checkpoint_data

            checkpoint_data = loop.run_until_complete(load_async())
            loop.close()

            # Handle versioning and migration
            return self._load_checkpoint_with_migration(checkpoint_data, current_seed)

        except Exception as e:
            logger.warning(f"Failed to load checkpoint with new system: {str(e)}")
            # Fallback to legacy loading for backward compatibility
            return self._load_checkpoint_legacy(current_seed)

    def _load_checkpoint_with_migration(self, checkpoint_data: Dict[str, Any], current_seed: Optional[int] = None) -> bool:
        """
        Load checkpoint data with migration support.

        Args:
            checkpoint_data: Raw checkpoint data
            current_seed: Optional current world seed to verify against

        Returns:
            True if checkpoint loaded successfully
        """
        try:
            # Extract version information
            schema_version_str = checkpoint_data.get("schema_version", str(self.LEGACY_CHECKPOINT_VERSION))
            loaded_version = self.version_manager.parse_version(schema_version_str)

            # Check seed if provided
            saved_seed = checkpoint_data.get("world_seed")
            if current_seed is not None and saved_seed is not None:
                if current_seed != saved_seed:
                    logger.warning(f"Seed mismatch (current={current_seed}, saved={saved_seed}). Resetting state.")
                    return False

            # Migrate if necessary
            if loaded_version < self.current_schema_version:
                logger.info(f"Migrating checkpoint from version {loaded_version} to {self.current_schema_version}")

                migration_result = self.migration_manager.migrate_checkpoint(
                    checkpoint_data,
                    loaded_version,
                    self.current_schema_version
                )

                if not migration_result.success:
                    logger.error(f"Migration failed: {migration_result.errors}")
                    # Try to load original data as fallback
                    if migration_result.warnings:
                        logger.warning(f"Migration warnings: {migration_result.warnings}")

                checkpoint_data = migration_result.data
                loaded_version = self.current_schema_version

            elif loaded_version > self.current_schema_version:
                logger.warning(f"Checkpoint version {loaded_version} is newer than current schema {self.current_schema_version}. "
                             "Some features may not be available.")

            # Load the (possibly migrated) data
            self._apply_checkpoint_data(checkpoint_data)
            self.loaded_schema_version = loaded_version

            logger.info(f"Checkpoint loaded successfully (version {loaded_version})")
            return True

        except Exception as e:
            logger.error(f"Failed to load checkpoint with migration: {str(e)}")
            return False

    def _load_checkpoint_legacy(self, current_seed: Optional[int] = None) -> bool:
        """
        Legacy checkpoint loading for backward compatibility.

        Args:
            current_seed: Optional current world seed to verify against

        Returns:
            True if checkpoint loaded successfully
        """
        filepath = self.checkpoint_dir / self.CHECKPOINT_FILE

        if not filepath.exists():
            return False

        try:
            with open(filepath) as f:
                data = json.load(f)

            # Check seed if provided
            saved_seed = data.get("world_seed")
            if current_seed is not None and saved_seed is not None:
                if current_seed != saved_seed:
                    logger.warning(f"Seed mismatch (current={current_seed}, saved={saved_seed}). Resetting state.")
                    return False

            # Apply legacy data with version 0.0.0
            self._apply_checkpoint_data(data)
            self.loaded_schema_version = self.LEGACY_CHECKPOINT_VERSION

            logger.info("Legacy checkpoint loaded successfully")
            return True

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Failed to load legacy checkpoint: {str(e)}")
            return False

    def _apply_checkpoint_data(self, data: Dict[str, Any]) -> None:
        """
        Apply checkpoint data to current state.

        Args:
            data: Checkpoint data dictionary
        """
        self.current_phase = Phase[data["phase"]]
        self._last_position = tuple(data["position"])
        self.phase_progress = {
            Phase[name]: val
            for name, val in data.get("phase_progress", {}).items()
        }
        self.custom_data = data.get("custom_data", {})
        payloads = data.get("phase_payloads", {})
        if isinstance(payloads, dict):
            self.phase_payloads = {name: dict(value) for name, value in payloads.items()}
    
    def clear_checkpoint(self) -> None:
        """Delete checkpoint file."""
        filepath = self.checkpoint_dir / self.CHECKPOINT_FILE
        if filepath.exists():
            filepath.unlink()
    
    def is_phase_complete(self, phase: Optional[Phase] = None) -> bool:
        """Check if a phase is complete (progress >= 1.0)."""
        return self.get_progress(phase) >= 1.0
    
    def get_phase_index(self) -> int:
        """Get numeric index of current phase."""
        return list(Phase).index(self.current_phase)
    
    def get_total_phases(self) -> int:
        """Get total number of phases."""
        return len(Phase)
    
    def get_overall_progress(self) -> float:
        """Get overall progress as percentage."""
        total_phases = len(Phase) - 1  # Exclude COMPLETE
        if total_phases == 0:
            return 1.0

        completed = sum(1 for p in Phase if p != Phase.COMPLETE and self.phase_progress.get(p, 0) >= 1.0)
        return completed / total_phases

    def get_loaded_schema_version(self) -> Optional[SemanticVersion]:
        """Get the schema version of the currently loaded checkpoint."""
        return self.loaded_schema_version

    def can_rollback_checkpoint(self) -> bool:
        """
        Check if checkpoint rollback is possible.

        Returns:
            True if rollback is supported
        """
        return self.migration_manager.can_rollback()

    def rollback_checkpoint(self) -> Optional[Dict[str, Any]]:
        """
        Rollback to previous checkpoint state.

        Returns:
            Previous checkpoint data if rollback successful, None otherwise
        """
        try:
            previous_data = self.migration_manager.rollback_checkpoint()
            if previous_data:
                self._apply_checkpoint_data(previous_data)
                logger.info("Checkpoint rollback successful")
            return previous_data
        except Exception as e:
            logger.error(f"Checkpoint rollback failed: {str(e)}")
            return None

    def get_migration_history(self) -> List[Dict[str, Any]]:
        """Get migration operation history."""
        return self.migration_manager.get_migration_history()

    def validate_checkpoint_integrity(self) -> Dict[str, Any]:
        """
        Validate current checkpoint integrity.

        Returns:
            Validation report with any issues found
        """
        report = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'schema_version': str(self.current_schema_version),
            'loaded_version': str(self.loaded_schema_version) if self.loaded_schema_version else None
        }

        try:
            # Check required fields
            required_fields = ['phase', 'position', 'inventory_summary', 'timestamp']
            for field in required_fields:
                if not hasattr(self, f'_{field}') and field not in ['phase', 'position', 'inventory_summary', 'timestamp']:
                    # Handle special cases
                    if field == 'phase' and not hasattr(self, 'current_phase'):
                        report['errors'].append(f"Missing required field: {field}")
                    elif field == 'position' and not hasattr(self, '_last_position'):
                        report['errors'].append(f"Missing required field: {field}")

            # Check phase validity
            if hasattr(self, 'current_phase'):
                if not isinstance(self.current_phase, Phase):
                    report['errors'].append("Invalid phase type")

            # Check position validity
            if hasattr(self, '_last_position'):
                if not isinstance(self._last_position, tuple) or len(self._last_position) != 3:
                    report['errors'].append("Invalid position format")

            # Check phase progress validity
            if hasattr(self, 'phase_progress'):
                for phase, progress in self.phase_progress.items():
                    if not isinstance(progress, (int, float)) or not (0.0 <= progress <= 1.0):
                        report['warnings'].append(f"Invalid progress value for phase {phase}: {progress}")

            if report['errors']:
                report['valid'] = False

        except Exception as e:
            report['valid'] = False
            report['errors'].append(f"Validation error: {str(e)}")

        return report

    def get_checkpoint_info(self) -> Dict[str, Any]:
        """
        Get comprehensive information about the current checkpoint state.

        Returns:
            Dictionary with checkpoint metadata and statistics
        """
        return {
            'current_phase': self.current_phase.name if self.current_phase else None,
            'schema_version': str(self.current_schema_version),
            'loaded_version': str(self.loaded_schema_version) if self.loaded_schema_version else None,
            'position': self._last_position,
            'phase_progress': {p.name: v for p, v in self.phase_progress.items()},
            'overall_progress': self.get_overall_progress(),
            'custom_data_keys': list(self.custom_data.keys()) if self.custom_data else [],
            'phase_payloads_count': len(self.phase_payloads),
            'migration_history_count': len(self.get_migration_history()),
            'serialization_format': self.serialization_format.value,
            'storage_type': 'distributed' if hasattr(self, 'storage_manager') else 'filesystem'
        }
