from typing import Dict, Any

from .enums import MovementStatus, PathCalculationResultType, PathingCommandType
from .models import BlockPos, BetterBlockPos, Goal, Selection


def serialize_goal_block(pos: BlockPos) -> Goal:
    return Goal(goal_type="GoalBlock", payload=pos.to_dict())


def serialize_goal_xz(x: int, z: int) -> Goal:
    return Goal(goal_type="GoalXZ", payload={"x": int(x), "z": int(z)})


def serialize_goal_y_level(y: int) -> Goal:
    return Goal(goal_type="GoalYLevel", payload={"y": int(y)})


def serialize_selection(selection: Selection) -> Dict[str, Any]:
    return selection.to_dict()


def serialize_enum(value: Any) -> str:
    if isinstance(value, (MovementStatus, PathCalculationResultType, PathingCommandType)):
        return value.value
    raise TypeError(f"Unsupported enum type: {type(value)}")


def validate_coordinate(value: int, name: str) -> int:
    try:
        int_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return int_value


def validate_setting(name: str, value: Any) -> Any:
    # Placeholder for mapping baritone.api.Settings types. Enforces simple primitives for now.
    if isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError(f"Unsupported setting type for {name}: {type(value).__name__}")


def better_block_pos(x: int, y: int, z: int) -> BetterBlockPos:
    return BetterBlockPos(x=validate_coordinate(x, "x"), y=validate_coordinate(y, "y"), z=validate_coordinate(z, "z"))
