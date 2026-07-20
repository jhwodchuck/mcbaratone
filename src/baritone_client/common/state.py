"""
State management - World and player state tracking with persistence.
"""

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, Any, Tuple


@dataclass
class PlayerState:
    """Snapshot of player state."""
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    health: float
    food: int
    dimension: str
    is_dead: bool = False
    
    @property
    def position(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)
    
    @property
    def is_low_health(self) -> bool:
        return self.health < 10.0
        
    @property
    def is_hungry(self) -> bool:
        return self.food < 10


class WorldState:
    """
    Tracks player and world state with automatic refresh.
    
    Usage:
        state = WorldState(client)
        state.refresh()
        print(f"Position: {state.position}")
        print(f"Health: {state.health}")
    """
    
    def __init__(self, client, checkpoint_dir: Optional[str] = None):
        """
        Initialize world state tracker.
        
        Args:
            client: Baritone client instance
            checkpoint_dir: Directory for checkpoint files (default: cwd)
        """
        self.client = client
        if checkpoint_dir:
            self.checkpoint_dir = Path(checkpoint_dir)
        else:
            # Parallel controllers must never share compatibility checkpoints
            # through the repository working directory.  The supervisor gives
            # every bot an isolated MC_RUN_DIR; direct --run-dir launches set
            # the same variable in spawn_to_dragon.py.
            self.checkpoint_dir = Path(os.environ.get("MC_RUN_DIR", Path.cwd()))
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._last_state: Optional[PlayerState] = None
        self._last_refresh = 0.0
        self._checkpoints: Dict[str, Any] = {}
    
    def refresh(self) -> PlayerState:
        """
        Refresh state from bridge.
        
        Returns:
            Current player state
        """
        try:
            data = self.client.transport.dispatch("get_state", {})
            position = data.get("position", {})
            
            self._last_state = PlayerState(
                x=position.get("x", data.get("x", 0)),
                y=position.get("y", data.get("y", 0)),
                z=position.get("z", data.get("z", 0)),
                yaw=position.get("yaw", data.get("yaw", 0)),
                pitch=position.get("pitch", data.get("pitch", 0)),
                health=data.get("health", data.get("max_health", 20)),
                food=data.get("food_level", data.get("food", 20)),
                dimension=data.get("dimension", "minecraft:overworld"),
                is_dead=data.get("is_dead", False),
            )
            self._last_refresh = time.time()
            
            return self._last_state
            
        except Exception as e:
            if self._last_state:
                return self._last_state
            raise
    
    @property
    def position(self) -> Tuple[float, float, float]:
        """Get last known position."""
        if self._last_state:
            return self._last_state.position
        return (0, 64, 0)
    
    @property
    def health(self) -> float:
        """Get last known health."""
        return self._last_state.health if self._last_state else 20.0
    
    @property
    def food(self) -> int:
        """Get last known food level."""
        return self._last_state.food if self._last_state else 20
    
    @property
    def dimension(self) -> str:
        """Get current dimension."""
        return self._last_state.dimension if self._last_state else "minecraft:overworld"
    
    @property
    def is_dead(self) -> bool:
        """Check if player is dead."""
        return self._last_state.is_dead if self._last_state else False
    
    @property
    def is_in_nether(self) -> bool:
        """Check if in nether dimension."""
        return "nether" in self.dimension.lower()
    
    @property
    def is_in_end(self) -> bool:
        """Check if in end dimension."""
        return "the_end" in self.dimension.lower()
    
    def save_checkpoint(self, name: str, data: Optional[Dict] = None) -> str:
        """
        Save checkpoint with current state.
        
        Args:
            name: Checkpoint name
            data: Additional data to save
            
        Returns:
            Path to checkpoint file
        """
        checkpoint = {
            "name": name,
            "timestamp": time.time(),
            "state": asdict(self._last_state) if self._last_state else None,
            "data": data or {},
        }
        
        self._checkpoints[name] = checkpoint
        
        filepath = self.checkpoint_dir / f"checkpoint_{name}.json"
        with open(filepath, "w") as f:
            json.dump(checkpoint, f, indent=2)
        
        return str(filepath)
    
    def load_checkpoint(self, name: str) -> Optional[Dict]:
        """
        Load checkpoint by name.
        
        Returns:
            Checkpoint data or None if not found
        """
        if name in self._checkpoints:
            return self._checkpoints[name]
        
        filepath = self.checkpoint_dir / f"checkpoint_{name}.json"
        if filepath.exists():
            with open(filepath) as f:
                checkpoint = json.load(f)
                self._checkpoints[name] = checkpoint
                return checkpoint
        
        return None
    
    def poll_events(self) -> list:
        """
        Poll for new events from bridge.
        
        Returns:
            List of events (chat, damage, death)
        """
        try:
            data = self.client.transport.dispatch("get_events", {})
            if isinstance(data, dict):
                return data.get("events", [])
        except:
            pass
        return []
