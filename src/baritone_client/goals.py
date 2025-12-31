from typing import Dict

from .models import BlockPos, Goal
from .serialization import serialize_goal_block, serialize_goal_xz, serialize_goal_y_level
from .transport import Transport


class GoalFactory:
    @staticmethod
    def goal_block(x: int, y: int, z: int) -> Goal:
        return serialize_goal_block(BlockPos(int(x), int(y), int(z)))

    @staticmethod
    def goal_xz(x: int, z: int) -> Goal:
        return serialize_goal_xz(x, z)

    @staticmethod
    def goal_y_level(y: int) -> Goal:
        return serialize_goal_y_level(y)


class GoalManager:
    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    def apply(self, goal: Goal) -> Dict[str, object]:
        return self.transport.dispatch("goal/apply", goal.to_dict())

    def clear(self) -> Dict[str, object]:
        return self.transport.dispatch("goal/clear", {})
