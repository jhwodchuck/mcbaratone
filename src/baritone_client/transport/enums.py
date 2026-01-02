from enum import Enum


class MovementStatus(Enum):
    RUNNING = "running"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETE = "complete"


class PathCalculationResultType(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PathingCommandType(Enum):
    REQUEST_PAUSE = "request_pause"
    DELAY = "delay"
    REVALIDATE_GOAL = "revalidate_goal"
    MOVE_TO = "move_to"


class TransportEvent(Enum):
    CHAT = "chat"
    RENDER = "render"
    TICK = "tick"
    PLAYER_UPDATE = "player_update"
    MOVEMENT_STATUS = "movement_status"
    # Additional event types from bridge
    BLOCK_INTERACT = "block_interact"
    ENTITY_SPAWN = "entity_spawn"
    ENTITY_MOVE = "entity_move"
    ENTITY_DESPAWN = "entity_despawn"
    PATHFINDING_STATE = "pathfinding_state"
    MISSION = "mission"
    TICK_UPDATE = "tick_update"
    DIMENSION_CHANGE = "dimension_change"
    INVENTORY_CHANGE = "inventory_change"
    DEATH = "death"
    RESPAWN = "respawn"
    ENTITY_UPDATE = "entity_update"
    DAMAGE = "damage"
