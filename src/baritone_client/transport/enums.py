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
    # Phase 3: Additional event types
    # Block modification events
    BLOCK_BREAK = "block_break"
    BLOCK_PLACE = "block_place"
    BLOCK_UPDATE = "block_update"
    # Entity interaction events
    ENTITY_DAMAGE = "entity_damage"
    ENTITY_ATTACK = "entity_attack"
    ENTITY_TRADE = "entity_trade"
    ENTITY_TAME = "entity_tame"
    ENTITY_SHEAR = "entity_shear"
    ENTITY_MILK = "entity_milk"
    # Environment events
    WEATHER_CHANGE = "weather_change"
    TIME_CHANGE = "time_change"
    TIME_UPDATE = "time_update"
    REDSTONE_UPDATE = "redstone_update"
    # Multiplayer events
    PLAYER_JOIN = "player_join"
    PLAYER_LEAVE = "player_leave"
    CHAT_MESSAGE = "chat_message"
