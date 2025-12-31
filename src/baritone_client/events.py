from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from .enums import TransportEvent


@dataclass
class EventListener:
    event: TransportEvent
    callback: Callable[[Dict[str, Any]], None]


class EventRegistry:
    def __init__(self) -> None:
        self._listeners: List[EventListener] = []

    def subscribe(self, event: TransportEvent, callback: Callable[[Dict[str, Any]], None]) -> None:
        self._listeners.append(EventListener(event=event, callback=callback))

    def dispatch(self, event: TransportEvent, payload: Dict[str, Any]) -> None:
        for listener in self._listeners:
            if listener.event == event:
                listener.callback(payload)
