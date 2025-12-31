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
