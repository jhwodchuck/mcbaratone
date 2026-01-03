"""
State Manager - Handles phase progression and checkpoint persistence.
"""

import json
import os
from enum import Enum, auto
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from pathlib import Path


class Phase(Enum):
    """Phases of end-game progression."""
    BRIDGE_CHECK = auto()       # Verify bridge connectivity and APIs
    BOOT_SEQUENCE = auto()      # Phase 1: Shelter, Food, Tools
    FOOD_AND_IRON = auto()      # Phase 2: Food independence, Iron gear
    ENCHANTING_PIPELINE = auto()# Phase 3: Level 30 enchanting
    NETHER_AND_BLAZE = auto()   # Phase 4: Blaze rods
    VILLAGER_INFRA = auto()     # Phase 5: Villager breeder operational
    XP_ENGINE = auto()          # Phase 6: Infinite XP
    IRON_FARM = auto()          # Phase 7: Infinite iron
    TOOL_PERFECTION = auto()    # Phase 8: Mending economy
    WORLD_UNLOCK = auto()       # Phase 9: End access and shulker boxes
    MEGABASE_INIT = auto()      # Phase 10: Megabase initialization
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
    Manages phase progression and checkpoint persistence.
    
    Provides:
    - Current phase tracking
    - Phase advancement with validation
    - Checkpoint save/load for crash recovery
    - Progress percentage per phase
    """
    
    CHECKPOINT_FILE = "spawn_to_dragon_checkpoint.json"
    
    
    def __init__(self, checkpoint_dir: Optional[str] = None):
        """
        Initialize state manager.
        """
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else Path.cwd()
        self.current_phase = Phase.BRIDGE_CHECK
        self.phase_progress: Dict[Phase, float] = {p: 0.0 for p in Phase}
        self.custom_data: Dict[str, Any] = {}
        self.phase_payloads: Dict[str, Dict[str, Any]] = {}
        self._last_position: tuple[float, float, float] = (0, 64, 0)
        
        # Phase Readiness Scores
        self.phase_readiness: Dict[Phase, float] = {}
        
        # Phase conditions placeholder (to be implemented later)
        self.phase_conditions: Dict[Phase, Any] = {}

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
        Save current state to checkpoint file.
        
        Args:
            inventory_summary: Dict of item_id -> count
            world_seed: Optional world seed to store
            
        Returns:
            Path to saved checkpoint file
        """
        import time
        
        checkpoint = {
            "phase": self.current_phase.name,
            "position": list(self._last_position),
            "inventory_summary": inventory_summary,
            "timestamp": time.time(),
            "phase_progress": {p.name: v for p, v in self.phase_progress.items()},
            "custom_data": self.custom_data,
            "phase_payloads": self.phase_payloads,
            "world_seed": world_seed
        }
        
        filepath = self.checkpoint_dir / self.CHECKPOINT_FILE
        with open(filepath, "w") as f:
            json.dump(checkpoint, f, indent=2)
        
        return str(filepath)
    
    def load_checkpoint(self, current_seed: Optional[int] = None) -> bool:
        """
        Load state from checkpoint file.
        
        Args:
            current_seed: Optional current world seed to verify against
            
        Returns:
            True if checkpoint loaded, False if not found or seed mismatch
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
                    print(f"  Seed mismatch (current={current_seed}, saved={saved_seed}). Resetting state.")
                    return False
            
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
            
            return True
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            print(f"Warning: Failed to load checkpoint: {e}")
            return False
    
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
