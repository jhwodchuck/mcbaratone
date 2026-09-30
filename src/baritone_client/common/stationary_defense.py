"""Context-local movement restriction for failed-escape defensive fights."""

from contextvars import ContextVar

_OWNERS: ContextVar[tuple[object, ...]] = ContextVar(
    "mcbaratone_stationary_defense", default=()
)


def stationary_defense_active(client) -> bool:
    """Do not leak a last-resort restriction to another client or thread."""
    return any(owner is client for owner in _OWNERS.get())


def stationary_pursuit_refused(client, distance) -> bool:
    """Require a known finite melee distance before in-place defense."""
    if not stationary_defense_active(client):
        return False
    try:
        return not 0 <= float(distance) < 4.5
    except (TypeError, ValueError):
        return True


def fight_without_pursuit(client, fight, target, **kwargs) -> bool:
    """Allow in-place defense, not a chase disguised as failed evasion."""
    token = _OWNERS.set((*_OWNERS.get(), client))
    try:
        return fight(client, target, **kwargs)
    finally:
        _OWNERS.reset(token)
