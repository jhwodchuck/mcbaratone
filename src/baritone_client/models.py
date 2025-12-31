from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class BlockPos:
    x: int
    y: int
    z: int

    def to_dict(self) -> Dict[str, int]:
        return {"x": self.x, "y": self.y, "z": self.z}

    @classmethod
    def from_tuple(cls, coords: Tuple[int, int, int]) -> "BlockPos":
        if len(coords) != 3:
            raise ValueError("BlockPos requires exactly three coordinates")
        x, y, z = coords
        return cls(int(x), int(y), int(z))


@dataclass(frozen=True)
class BetterBlockPos(BlockPos):
    def offset(self, dx: int = 0, dy: int = 0, dz: int = 0) -> "BetterBlockPos":
        return BetterBlockPos(self.x + dx, self.y + dy, self.z + dz)


@dataclass(frozen=True)
class Selection:
    start: BetterBlockPos
    end: BetterBlockPos

    def to_dict(self) -> Dict[str, Dict[str, int]]:
        return {"start": self.start.to_dict(), "end": self.end.to_dict()}


@dataclass(frozen=True)
class Goal:
    goal_type: str
    payload: Dict[str, int]

    def to_dict(self) -> Dict[str, Dict[str, int]]:
        return {"type": self.goal_type, "payload": self.payload}
