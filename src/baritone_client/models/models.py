from typing import Any, Dict, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field

class BlockPos(BaseModel):
    x: int
    y: int
    z: int

    model_config = ConfigDict(frozen=True)

    def to_dict(self) -> Dict[str, int]:
        return self.model_dump()

    @classmethod
    def from_tuple(cls, coords: Tuple[int, int, int]) -> "BlockPos":
        if len(coords) != 3:
            raise ValueError("BlockPos requires exactly three coordinates")
        return cls(x=int(coords[0]), y=int(coords[1]), z=int(coords[2]))


class BetterBlockPos(BlockPos):
    def offset(self, dx: int = 0, dy: int = 0, dz: int = 0) -> "BetterBlockPos":
        return BetterBlockPos(x=self.x + dx, y=self.y + dy, z=self.z + dz)


class Selection(BaseModel):
    start: BetterBlockPos
    end: BetterBlockPos

    model_config = ConfigDict(frozen=True)

    def to_dict(self) -> Dict[str, Dict[str, int]]:
        return self.model_dump()


class Goal(BaseModel):
    goal_type: str = Field(alias="type")
    payload: Dict[str, Any]

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True)
