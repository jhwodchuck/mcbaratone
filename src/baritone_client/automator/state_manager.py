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
    SPAWN_BOOTSTRAP = auto()    # Explore spawn, bootstrap mission macros
    INITIAL_GATHERING = auto()  # Wood, stone, food, basic tools
    BASE_CONSTRUCTION = auto()  # Shelter, furnace, crafting table
    IRON_AGE = auto()           # Iron tools and armor
    DIAMOND_MINING = auto()     # Diamond tools and armor
    ENCHANTING = auto()         # Enchanted equipment
    NETHER_PREP = auto()        # Obsidian, flint and steel
    NETHER_TRAVEL = auto()      # Blaze rods, gold
    ENDER_PEARL_FARM = auto()   # Kill endermen for pearls
    STRONGHOLD_LOCATE = auto()  # Use eyes to find stronghold
    END_PORTAL = auto()         # Activate and enter portal
    DRAGON_FIGHT = auto()       # Fight the dragon
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
        
        # Default Transition Conditions (Example setup)
        # Ideally this is loaded from config, but code definition works for now.
        self.phase_conditions: Dict[Phase, Any] = {
            Phase.INITIAL_GATHERING: [
                PhaseCondition("wood_gathered", "Have 16 logs", weight=0.5, check_fn=lambda c: (False, 0)),
                PhaseCondition("stone_gathered", "Have 16 cobble", weight=0.5),
            ],
            # ... others ...
        }

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
    
    def add_location(self, category: str, x: int, y: int, z: int, dimension: str = "overworld", tags: Optional[list] = None) -> None:
        """
        Record a location of interest.
        
        Args:
            category: Type of location (e.g. 'chest', 'bed', 'portal')
            x, y, z: Coordinates
            dimension: Dimension name
            tags: Optional tags
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
        
    def get_locations(self, category: str = None) -> Dict[str, list]:
        """Get recorded locations (filtered by category if provided)."""
        locs = self.custom_data.get("locations", {})
        if category:
            return {category: locs.get(category, [])}
        return locs
    
    def get_phase_payload(self, phase: Phase) -> Dict[str, Any]:
        """Retrieve stored payload for a phase."""
        return self.phase_payloads.get(phase.name, {})
    
    def save_checkpoint(self, inventory_summary: Dict[str, int]) -> str:
        """
        Save current state to checkpoint file.
        
        Args:
            inventory_summary: Dict of item_id -> count
            
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
        }
        
        filepath = self.checkpoint_dir / self.CHECKPOINT_FILE
        with open(filepath, "w") as f:
            json.dump(checkpoint, f, indent=2)
        
        return str(filepath)
    
    def load_checkpoint(self) -> bool:
        """
        Load state from checkpoint file.
        
        Returns:
            True if checkpoint loaded, False if not found
        """
        filepath = self.checkpoint_dir / self.CHECKPOINT_FILE
        
        if not filepath.exists():
            return False
        
        try:
            with open(filepath) as f:
                data = json.load(f)
            
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
