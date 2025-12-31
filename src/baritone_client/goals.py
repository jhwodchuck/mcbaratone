from typing import Dict

from .models import BlockPos, Goal
from .serialization import serialize_goal_block, serialize_goal_xz, serialize_goal_y_level
from .transport import Transport


class GoalFactory:
    """Factory for creating goal objects."""
    
    @staticmethod
    def goal_block(x: int, y: int, z: int) -> Goal:
        """
        Create a goal to reach a specific block position.
        
        Args:
            x: X coordinate
            y: Y coordinate
            z: Z coordinate
        
        Returns:
            Goal object
        """
        return serialize_goal_block(BlockPos(x=int(x), y=int(y), z=int(z)))

    @staticmethod
    def goal_xz(x: int, z: int) -> Goal:
        """
        Create a goal to reach a specific XZ coordinate (any Y level).
        
        Args:
            x: X coordinate
            z: Z coordinate
        
        Returns:
            Goal object
        """
        return serialize_goal_xz(x, z)

    @staticmethod
    def goal_y_level(y: int) -> Goal:
        """
        Create a goal to reach a specific Y level.
        
        Args:
            y: Y coordinate/level
        
        Returns:
            Goal object
        """
        return serialize_goal_y_level(y)


class GoalManager:
    """Manager for applying and clearing Baritone goals."""
    
    def __init__(self, transport: Transport) -> None:
        """
        Initialize goal manager.
        
        Args:
            transport: Transport instance
        """
        self.transport = transport

    def apply(self, goal: Goal) -> Dict[str, object]:
        """
        Apply a goal to Baritone.
        
        Args:
            goal: Goal object to apply
        
        Returns:
            Response dictionary
        
        Raises:
            CommandError: If goal application fails
        """
        return self.transport.dispatch("goal/apply", goal.to_dict())

    def clear(self) -> Dict[str, object]:
        """
        Clear the current goal (stop pathfinding).
        
        Returns:
            Response dictionary
        
        Raises:
            CommandError: If goal clearing fails
        """
        return self.transport.dispatch("goal/clear", {})
